"""T1 零 LLM 刷新执行器：调 vibe-stock 已暴露 CLI 出新 context-pack（07 T1 预留位、M1 口径）。

链路（M1 用户裁定）：fetch.py 全量取数 → diagnose.py prepare --evidence 直入 → context-pack.md。
零 LLM、零机制复制（产品仓不 import vibe-stock 代码，只 CLI 调用，机制 SSOT 在外仓）。
锁纪律：调用方（worker）在 watch.lock/fetch_state.lock 在场时 defer（I-005 盯盘期间不跑全诊取数），
实际落在收盘后锁释放自动补跑；07 轻量日 K 链（重拉日 K+technical+快照拼 evidence）无 CLI 暴露，
登记为 vibe-stock 机制升级项。坑位：恒不传 --cost（用户成本永不进 workdir，NR-5）。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR_ENV = "STA_SKILL_DIR"  # 测试注入位；缺省 ~/.claude/skills/vibe-stock（软链）
UV_PATH_ENV = "STA_UV_PATH"  # 测试注入位；缺省 which(uv) → ~/.local/bin/uv

UV_PINS: tuple[str, ...] = (
    "--with",
    "akshare==1.20.*",
    "--with",
    "efinance",
    "--with",
    "yfinance",
)
FETCH_EXIT_DAILY_BARS_FAILED = 2  # fetch.py 契约：exit 2 = 日 K 全链失败
CONTEXT_PACK_NAME = "context-pack.md"  # prepare 产物（成功判据）

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def resolve_skill_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    return Path(env.get(SKILL_DIR_ENV, str(Path.home() / ".claude" / "skills" / "vibe-stock")))


def resolve_uv(env: dict[str, str] | None = None) -> str:
    """uv 可执行解析：env 注入 → PATH → ~/.local/bin/uv 回落（launchd 无 shell PATH）。"""
    env = dict(os.environ) if env is None else env
    if env.get(UV_PATH_ENV):
        return env[UV_PATH_ENV]
    import shutil

    found = shutil.which("uv")
    if found:
        return found
    return str(Path.home() / ".local" / "bin" / "uv")


def fetch_stock_arg(code: str) -> str:
    """fetch.py --stock 契约（detect_market）：cn 6 位原样；hk 5 位补 hk 前缀（hk00700 形态）。"""
    if len(code) == 5:
        return f"hk{code}"
    return code


def build_fetch_argv(uv: str, skill_dir: Path, code: str, out: Path) -> list[str]:
    return [
        uv,
        "run",
        "--no-project",
        *UV_PINS,
        str(skill_dir / "scripts" / "fetch.py"),
        "--stock",
        fetch_stock_arg(code),
        "--out",
        str(out),
    ]


def build_prepare_argv(
    uv: str, skill_dir: Path, code: str, workdir: Path, evidence: Path
) -> list[str]:
    # diagnose.py 是子命令形态（prepare --stock --workdir --evidence）
    return [
        uv,
        "run",
        "--no-project",
        *UV_PINS,
        str(skill_dir / "scripts" / "diagnose.py"),
        "prepare",
        "--stock",
        fetch_stock_arg(code),
        "--workdir",
        str(workdir),
        "--evidence",
        str(evidence),
    ]


@dataclass(frozen=True)
class T1Outcome:
    ok: bool
    workdir: Path
    reasons: tuple[str, ...]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False)


class T1Runner:
    """T1 刷新执行器（CommandRunner 可注入；测试零网络零子进程）。"""

    def __init__(
        self,
        *,
        skill_dir: Path | None = None,
        timeout_s: int = 600,
        run: CommandRunner | None = None,
    ) -> None:
        self.skill_dir = skill_dir if skill_dir is not None else resolve_skill_dir()
        self.timeout_s = timeout_s
        self._run = run or _default_runner

    def run(self, code: str, *, workdir: Path) -> T1Outcome:
        if not self.skill_dir.exists():
            return T1Outcome(
                ok=False, workdir=workdir, reasons=(f"SKILL_DIR 不存在：{self.skill_dir}",)
            )
        workdir.mkdir(parents=True, exist_ok=True)
        evidence = workdir / "evidence.json"
        uv = resolve_uv()

        try:
            fetch = self._run(build_fetch_argv(uv, self.skill_dir, code, evidence))
        except subprocess.TimeoutExpired:
            return T1Outcome(ok=False, workdir=workdir, reasons=("fetch 超时",))
        if fetch.returncode == FETCH_EXIT_DAILY_BARS_FAILED:
            return T1Outcome(ok=False, workdir=workdir, reasons=("fetch exit 2：日 K 全链失败",))
        if fetch.returncode != 0:
            return T1Outcome(ok=False, workdir=workdir, reasons=(f"fetch exit {fetch.returncode}",))

        try:
            prepare = self._run(build_prepare_argv(uv, self.skill_dir, code, workdir, evidence))
        except subprocess.TimeoutExpired:
            return T1Outcome(ok=False, workdir=workdir, reasons=("prepare 超时",))
        if prepare.returncode != 0:
            return T1Outcome(
                ok=False, workdir=workdir, reasons=(f"prepare exit {prepare.returncode}",)
            )
        if not (workdir / CONTEXT_PACK_NAME).exists():
            return T1Outcome(
                ok=False, workdir=workdir, reasons=(f"成功判据未满足：{CONTEXT_PACK_NAME} 未产出",)
            )
        return T1Outcome(ok=True, workdir=workdir, reasons=())
