"""HeadlessCLI 通道的可测半体：build_argv / sanitize_env 纯函数 + 真实 spawn 门。

真实子进程执行（asyncio + wait_for 超时 I-37、SIGINT 收轮 D-01、子进程持引用防 GC）为 stub：
默认 raise，仅 STA_ALLOW_REAL_CC=1 放行——「dry-run 默认」红线在代码层可见（AGENTS.md 测试门禁节）。
坑位：升级观察项 --bare 只认 API key 不读订阅 OAuth（D-04/F-29）；本骨架不使用 --bare。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from stock_trading_agent.cc_client.contract import CCRequest, CCResult, ProbeReport

CLI_BIN = "claude"
GATE_ENV = "STA_ALLOW_REAL_CC"


def build_argv(req: CCRequest, resume_session_id: str | None = None) -> list[str]:
    """构造 headless CLI 完整旗标面（快照测试防自侧旗标漂移，方案 §9 硬约束）。"""
    argv = [CLI_BIN, "-p", "--output-format", "stream-json", "--verbose"]
    if resume_session_id is None:
        argv += ["--session-id", req.session_uuid]
    else:
        argv += ["--resume", resume_session_id, "--session-id", req.session_uuid]
    if req.schema is not None:
        import json

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
        argv += ["--strict-mcp-config", "--mcp-config", os.devnull]
    if req.autocompact is not None:
        argv += ["--autocompact", req.autocompact]
    argv += ["--system-prompt-snapshot", "off"]  # 迭代 system prompt 须关闭快照（F-43）
    prompt = req.prompt if isinstance(req.prompt, str) else str(req.prompt)
    argv.append(prompt)
    return argv


def sanitize_env(env: Mapping[str, str]) -> dict[str, str]:
    """编排环境治理（方案 §13 认证分叉）。

    做法：剥离 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN（存在即静默脱离订阅计费，F-31）；
    注入 DISABLE_AUTOUPDATER=1 / DISABLE_AUTOUPDATES=1 钉死版本（方案 §14 版本治理）。
    坑位：这是全仓唯一允许触碰密钥环境变量名的位置，且只做剥离、永不读取其值（红线扫描测试锚点）。
    """
    out = {k: v for k, v in env.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}}
    out["DISABLE_AUTOUPDATER"] = "1"
    out["DISABLE_AUTOUPDATES"] = "1"
    return out


class RealSpawnDisabled(RuntimeError):
    """真实 CC spawn 被拒（骨架期默认关；STA_ALLOW_REAL_CC=1 放行）。"""


def _real_spawn_gate() -> None:
    if os.environ.get(GATE_ENV) != "1":
        raise RealSpawnDisabled(
            f"真实 CC 通道默认禁用（骨架期）：设 {GATE_ENV}=1 放行；CI/测试一律走 dryrun 通道"
        )


class HeadlessCLIClient:
    """真实 headless CLI 客户端（stub：spawn 面留 M1 完整版；契约与旗标构建已可用）。"""

    def spawn_once(self, req: CCRequest) -> CCResult:  # pragma: no cover - M1 完整版落地
        _real_spawn_gate()
        raise NotImplementedError(
            "M1 完整版落地：asyncio 子进程 + wait_for 超时 + SIGINT 收轮（§9 硬约束）"
        )

    def resume(self, session_id: str, req: CCRequest) -> CCResult:  # pragma: no cover
        _real_spawn_gate()
        raise NotImplementedError("M1 完整版落地：--resume 旗标全量重传（D-32）")

    def probe(self) -> ProbeReport:  # pragma: no cover
        _real_spawn_gate()
        raise NotImplementedError(
            "M1 完整版落地：--version + 认证态 + init.capabilities（F-10/F-31）"
        )

    def archive(self, req: CCRequest) -> None:  # pragma: no cover
        """转录归档（断言器放行后由 Loop 调用；真实转录来自官方 transcript 目录，F-38）。"""
        _real_spawn_gate()
        raise NotImplementedError(
            "M1 完整版落地：stream-json 归档至 agent/audit/<date>/（方案 §11）"
        )
