"""HeadlessCLI 通道：build_argv / sanitize_env 纯函数 + 真实 spawn 完整实现。

真实子进程纪律（方案 §9 硬约束 + 12-orchestration §②裁定）：
- asyncio 子进程 + wait_for 超时（I-37），子进程长期持引用防 GC（_live_procs）；
- 超时状态机：deadline → killpg(SIGINT)（收轮，宽限 SIGINT_GRACE_S）→ 仍活 killpg(SIGKILL)；
  编排器永不主动发 SIGTERM（D-01；外部 SIGTERM=143 弃轮语义由退出码归一化承载）；
- start_new_session=True 独立进程组（与 launchd AbandonProcessGroup=true 双保险，D3）；
- 退出码归一化：returncode<0 → 128+signo（130 收轮 / 137 SIGKILL 兜底 / 143 外部弃轮）；
- stream-json 逐行解析（_parse_stream 纯函数）：init 行取 session_id/capabilities/errors，
  末行 result 为 envelope；坏行/未知行不炸、原样进 raw 缓存（审计保真，R-03 版本漂移容错）；
- 归档 NR-3 次序：spawn 期 raw 行只驻内存（_pending），断言器放行后 archive() 落
  agent/audit/<date>/<session_uuid>.jsonl（F-38 当场落盘自有副本，官方转录默认 30 天清理）。
坑位：--bare 只认 API key 不读订阅 OAuth（D-04/F-29）——本通道永不使用；真实 spawn 默认拒，
STA_ALLOW_REAL_CC=1 放行（「dry-run 默认」红线在代码层可见）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stock_trading_agent.cc_client.contract import CCRequest, CCResult, ProbeReport

CLI_BIN = "claude"
GATE_ENV = "STA_ALLOW_REAL_CC"
SIGINT_GRACE_S = 10.0  # 超时后 SIGINT 收轮的宽限（超时即升级 SIGKILL）
PROBE_TIMEOUT_S = 15.0
CREDENTIALS_REL = Path(".claude") / ".credentials.json"  # 订阅 OAuth 登录态文件（probe 启发式）


def empty_mcp_config_path() -> str:
    """strict-empty 形态的空 mcp 配置文件（内容恒 `{"mcpServers": {}}`，幂等写）。

    坑位（2.1.319 实测）：`--mcp-config /dev/null` 报 Invalid MCP configuration
    （空文件非合法 JSON）；裸 `{}` 也不行（schema 要求 mcpServers 键为 record）——
    空配置必须是带空 mcpServers 的合法文件（D-53 的 429k→2.8k 优化不回退）。
    """
    import json as _json
    import tempfile

    payload = _json.dumps({"mcpServers": {}})
    directory = Path(tempfile.gettempdir()) / "stock-trading-agent"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "empty-mcp.json"
    if not path.exists() or path.read_text(encoding="utf-8") != payload:
        path.write_text(payload, encoding="utf-8")
    return str(path)


def build_argv(req: CCRequest, resume_session_id: str | None = None) -> list[str]:
    """构造 headless CLI 完整旗标面（快照测试防自侧旗标漂移，方案 §9 硬约束）。"""
    argv = [CLI_BIN, "-p", "--output-format", "stream-json", "--verbose"]
    if resume_session_id is None:
        argv += ["--session-id", req.session_uuid]
    else:
        argv += ["--resume", resume_session_id, "--session-id", req.session_uuid]
    if req.schema is not None:
        argv += ["--json-schema", json.dumps(req.schema, ensure_ascii=False)]
    argv += [
        "--max-budget-usd",
        f"{req.budget_usd:g}",
        "--max-turns",
        str(req.max_turns),
        "--permission-mode",
        req.permission_mode,
        "--allowedTools",
        ",".join(req.allowed_tools),
        "--permission-prompts",
        "none",  # 拒绝记入 permission_denials 审计流水（F-24/F-28）
    ]
    if req.mcp == "strict-empty":
        argv += ["--strict-mcp-config", "--mcp-config", empty_mcp_config_path()]
    if req.autocompact is not None:
        argv += ["--autocompact", req.autocompact]
    argv += ["--system-prompt-snapshot", "off"]  # 迭代 system prompt 须关闭快照（F-43）
    argv += ["--setting-sources", "project"]  # D-52 慢启动防御：禁 user/policy settings
    # （会话 cwd 恒空目录 ⇒ project 等价空集；143s→2s 实测）
    prompt = req.prompt if isinstance(req.prompt, str) else str(req.prompt)
    argv.append(prompt)
    return argv


def sanitize_env(env: Mapping[str, str]) -> dict[str, str]:
    """编排环境治理（方案 §13 认证分叉）。

    做法：剥离 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN（存在即静默脱离订阅计费，F-31）；
    注入 DISABLE_AUTOUPDATER=1 / DISABLE_AUTOUPDATES=1 钉死版本（方案 §14 版本治理）。
    坑位：这是全仓唯一允许触碰密钥环境变量名的位置，且只做剥离、永不读取其值（红线扫描锚点）。
    """
    out = {k: v for k, v in env.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}}
    out["DISABLE_AUTOUPDATER"] = "1"
    out["DISABLE_AUTOUPDATES"] = "1"
    return out


def normalize_exit_code(rc: int) -> int:
    """退出码归一化：负 returncode（信号死）→ 128+signo（130 收轮/137 SIGKILL/143 外部弃轮）。"""
    return rc if rc >= 0 else 128 - rc


@dataclass
class _Parsed:
    """stream-json 解析产物（_parse_stream 纯函数输出）。"""

    envelope: dict[str, Any] | None = None
    result_line_present: bool = False
    structured_output: dict[str, Any] | None = None
    usage_tokens: dict[str, int] = field(default_factory=dict)
    session_id: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    mcp_server_errors: list[dict[str, Any]] = field(default_factory=list)
    permission_denials: int = 0


def _parse_stream(lines: list[str]) -> _Parsed:
    """解析 stream-json 行序列：init 行取能力面，末个 result 行为 envelope；坏行跳过不抛。"""
    parsed = _Parsed()
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue  # 截断/坏行：原样留 raw 缓存（审计保真），解析跳过（D-05 容错）
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            parsed.session_id = obj.get("session_id")
            caps = obj.get("capabilities")
            if isinstance(caps, dict):
                parsed.capabilities = caps
            errs = obj.get("mcp_server_errors")
            if isinstance(errs, list):
                parsed.mcp_server_errors = [e for e in errs if isinstance(e, dict)]
        elif obj.get("type") == "result":  # 末个 result 行定结局（D-06）
            parsed.result_line_present = True
            parsed.envelope = obj
            pd = obj.get("permission_denials")  # F-28 审计流水：int 或 list 双容错缺省 0
            parsed.permission_denials = (
                pd if isinstance(pd, int) else len(pd) if isinstance(pd, list) else 0
            )
            out = obj.get("structured_output")
            parsed.structured_output = out if isinstance(out, dict) else None
            usage = obj.get("usage")
            if isinstance(usage, dict):
                parsed.usage_tokens = {k: int(v) for k, v in usage.items() if isinstance(v, int)}
    return parsed


class RealSpawnDisabled(RuntimeError):
    """真实 CC spawn 被拒（默认关；STA_ALLOW_REAL_CC=1 放行）。"""


class ProbeError(RuntimeError):
    """probe 失败（CLI 不在 PATH / --version 超时）。"""


def _real_spawn_gate() -> None:
    if os.environ.get(GATE_ENV) != "1":
        raise RealSpawnDisabled(
            f"真实 CC 通道默认禁用：设 {GATE_ENV}=1 放行；CI/测试一律走 dryrun 通道"
        )


def _run_sync(coro):
    """同步壳：线程内无 running loop 时 asyncio.run；async 误用回退单线程池（防嵌套环）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class HeadlessCLIClient:
    """真实 headless CLI 客户端（spawn_once / resume / probe / archive 完整实现）。"""

    def __init__(
        self,
        audit_dir: Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
        cli_bin: str = CLI_BIN,
    ) -> None:
        self._audit_dir = audit_dir
        self._env = dict(os.environ) if env is None else dict(env)
        self._home = Path.home() if home is None else home
        self._cli_bin = cli_bin
        self._live_procs: set[asyncio.subprocess.Process] = set()  # I-37：持引用防 GC
        self._procs_lock = threading.Lock()
        self._pending: dict[str, list[str]] = {}  # NR-3：spawn 期 raw 行内存缓存

    # --- spawn 面 ---
    def spawn_once(self, req: CCRequest) -> CCResult:
        _real_spawn_gate()
        return _run_sync(self._execute(build_argv(req), req))

    def resume(self, session_id: str, req: CCRequest) -> CCResult:
        _real_spawn_gate()
        # 旗标全量重传（D-32）：build_argv 以 resume_session_id 重建完整旗标面
        return _run_sync(self._execute(build_argv(req, session_id), req))

    async def _execute(self, argv: list[str], req: CCRequest) -> CCResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,  # -p 从不读 stdin；继承会触发 3s 等待（实测坑）
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sanitize_env(self._env),
            cwd=req.cwd,
            start_new_session=True,  # 独立进程组：killpg 不误伤面板（D3 双保险）
        )
        with self._procs_lock:
            self._live_procs.add(proc)
        try:
            try:
                stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=req.timeout_s)
            except TimeoutError:
                await self._terminate(proc)  # SIGINT 收轮 → 宽限 → SIGKILL（永不主动 SIGTERM）
                await proc.wait()
                stdout = b""
                if proc.stdout is not None:  # communicate 已被取消：收尸后读余量（截断容忍）
                    with contextlib.suppress(Exception):
                        stdout = await proc.stdout.read()
        finally:
            with self._procs_lock:
                self._live_procs.discard(proc)
        return self._assemble(req, proc, stdout)

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """超时终止：killpg(SIGINT) 收轮 → 宽限 SIGINT_GRACE_S → killpg(SIGKILL)。"""
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=SIGINT_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)

    def _assemble(
        self, req: CCRequest, proc: asyncio.subprocess.Process, stdout: bytes
    ) -> CCResult:
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        self._pending[req.task_id] = lines  # NR-3：断言器放行前只驻内存
        parsed = _parse_stream(lines)
        envelope = parsed.envelope
        if envelope is not None and parsed.session_id is None:
            parsed.session_id = envelope.get("session_id")
        transcript = None
        if envelope is not None and envelope.get("transcript_path"):
            transcript = Path(str(envelope["transcript_path"]))
        audit_path = (
            self._audit_path(req, parsed.session_id or req.session_uuid)
            if self._audit_dir is not None
            else None
        )
        return CCResult(
            exit_code=normalize_exit_code(proc.returncode if proc.returncode is not None else -1),
            envelope=envelope,
            structured_output=parsed.structured_output,
            result_line_present=parsed.result_line_present,
            transcript_path=transcript or (audit_path or Path("/dev/null")),
            usage_tokens=parsed.usage_tokens,
            mcp_server_errors=parsed.mcp_server_errors,
            permission_denials=parsed.permission_denials,
        )

    def live_pids(self) -> set[int]:
        """在管子进程 PID 快照（泄漏巡检差集基准，D-08）。"""
        with self._procs_lock:
            return {proc.pid for proc in self._live_procs}

    # --- 归档（断言器放行后由 Loop 调用；无子进程无网络，不设 spawn 门） ---
    def _audit_path(self, req: CCRequest, session_id: str) -> Path:
        import datetime as dt

        day = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
        return self._audit_dir / day / f"{session_id}.jsonl"  # type: ignore[operator]

    def archive(self, req: CCRequest) -> None:
        if self._audit_dir is None:
            return
        lines = self._pending.pop(req.task_id, None)
        if lines is None:
            return
        from stock_trading_agent.state.store import atomic_append_lines

        path = self._audit_path(req, req.session_uuid)
        atomic_append_lines(path, lines)  # W7：收编 store 单一入口（flock+fsync，原裸写点）

    # --- 探针 ---
    def probe(self, *, deep: bool = False) -> ProbeReport:
        """--version + 认证态判定；deep=True 追加 mini spawn 读 capabilities（真实花钱默认关）。"""
        _real_spawn_gate()
        version = _run_sync(self._probe_version())
        auth_mode = _detect_auth_mode(self._env, self._home / CREDENTIALS_REL)
        capabilities: dict[str, Any] = {}
        if deep:
            mini = CCRequest(
                task_id="probe",
                session_uuid="probe",
                prompt="ping",
                schema=None,
                budget_usd=0.5,
                max_turns=1,
                allowed_tools=(),  # 探针形态：无任务白名单（不称任务，不走 validate_request）
            )
            _run_sync(self._execute(build_argv(mini), mini))
            lines = self._pending.pop("probe", [])
            capabilities = _parse_stream(lines).capabilities
        return ProbeReport(cli_version=version, auth_mode=auth_mode, capabilities=capabilities)

    async def _probe_version(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_bin,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sanitize_env(self._env),
            )
        except FileNotFoundError as exc:
            raise ProbeError(f"claude CLI 不在 PATH：{self._cli_bin}") from exc
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise ProbeError(
                "--version 超时（D-09：启动挂起连 --version 都无输出的先例）"
            ) from None
        first = stdout.decode("utf-8", errors="replace").splitlines()
        if not first or proc.returncode != 0:
            raise ProbeError(f"--version 非零退出（{proc.returncode}）")
        return first[0].strip()

    # --- 升级门禁通道件（0.0.26，§② 升级门禁裁定；与 probe 职责边界：本件只服务 upgrade-gate） ---

    def capture_help(self) -> str:
        """spawn `--help` 取全文（升级门禁快照源）。COLUMNS 钉 100——描述换行随终端宽度漂移。"""
        _real_spawn_gate()
        return _run_sync(self._capture_help())

    async def _capture_help(self) -> str:
        env = dict(sanitize_env(self._env))
        env["COLUMNS"] = "100"
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_bin,
                "--help",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ProbeError(f"claude CLI 不在 PATH：{self._cli_bin}") from exc
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise ProbeError("--help 超时") from None
        if proc.returncode != 0 or not stdout:
            raise ProbeError(f"--help 非零退出或空输出（{proc.returncode}）")
        return stdout.decode("utf-8", errors="replace")

    def probe_enum_reject(
        self, flag: str = "--permission-mode", value: str = "__sta_invalid__"
    ) -> bool:
        """非法枚举探针（F-30 补位）：非法值应非零快退；exit 0 = 隐藏别名吃进 → False。

        实测（2.1.319）：`--permission-mode __sta_invalid__ --version` exit 1 秒拒零成本；
        --version 短路使探针不触会话。
        """
        _real_spawn_gate()
        return _run_sync(self._probe_enum_reject(flag, value))

    async def _probe_enum_reject(self, flag: str, value: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_bin,
                flag,
                value,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sanitize_env(self._env),
            )
        except FileNotFoundError as exc:
            raise ProbeError(f"claude CLI 不在 PATH：{self._cli_bin}") from exc
        try:
            await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            return False  # 挂起视同未拒（保守）
        return proc.returncode != 0


def _detect_auth_mode(env: Mapping[str, str], credentials_path: Path) -> str:
    """认证态启发式（F-31/D2；判不准一律按 api 收紧）。

    做法：环境密钥名在场→api（编排环境已 sanitize_env 剥离，此处兜底判定）；
    订阅 OAuth 登录态文件在场→subscription；其余→api（收紧）。
    坑位：只判密钥名在场、永不取值（R2 红线扫描锚点）；macOS Keychain 形态未覆盖，
    属启发式——配合「判不准收紧」与 deep 探针兜底。
    """
    if "ANTHROPIC_API_KEY" in env:
        return "api"
    if credentials_path.exists():
        return "subscription"
    return "api"
