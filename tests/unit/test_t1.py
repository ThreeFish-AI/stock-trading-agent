"""T1 刷新执行器：argv 契约快照 + fake runner 全路径（零网络零子进程）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stock_trading_agent.loop.t1 import (
    CONTEXT_PACK_NAME,
    T1Runner,
    build_fetch_argv,
    build_prepare_argv,
    fetch_stock_arg,
)

UV = "/usr/local/bin/uv"
SK = Path("/skills/vibe-stock")


class FakeResult:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _ok_runner(workdir: Path):
    """两命令都 exit 0 且落 context-pack.md 的 fake（argv 顺序无关判定）。"""

    def run(argv: list[str]) -> FakeResult:
        if any("diagnose.py" in a for a in argv):
            (workdir / CONTEXT_PACK_NAME).write_text("# pack", encoding="utf-8")
        return FakeResult(0)

    return run


class TestArgv:
    def test_fetch_argv_snapshot(self, tmp_path: Path):
        argv = build_fetch_argv(UV, SK, "600519", tmp_path / "e.json")
        assert argv[:3] == [UV, "run", "--no-project"]
        assert argv[3:9] == [
            "--with",
            "akshare==1.20.*",
            "--with",
            "efinance",
            "--with",
            "yfinance",
        ]
        assert argv[9:] == [
            str(SK / "scripts" / "fetch.py"),
            "--stock",
            "600519",
            "--out",
            str(tmp_path / "e.json"),
        ]

    def test_fetch_argv_hk_prefix(self, tmp_path: Path):
        argv = build_fetch_argv(UV, SK, "00700", tmp_path / "e.json")
        assert argv[argv.index("--stock") + 1] == "hk00700"

    def test_prepare_argv_snapshot(self, tmp_path: Path):
        argv = build_prepare_argv(UV, SK, "600519", tmp_path / "w", tmp_path / "e.json")
        assert argv[9] == str(SK / "scripts" / "diagnose.py")
        assert argv[10] == "prepare"
        assert argv[argv.index("--workdir") + 1] == str(tmp_path / "w")
        assert argv[argv.index("--evidence") + 1] == str(tmp_path / "e.json")
        # NR-5：恒不传 --cost（用户成本不进 workdir）
        assert "--cost" not in argv

    def test_fetch_stock_arg(self):
        assert fetch_stock_arg("600519") == "600519"
        assert fetch_stock_arg("00700") == "hk00700"


class TestT1Runner:
    def test_happy_path_produces_pack(self, tmp_path: Path):
        workdir = tmp_path / "runs" / "t1" / "600519"
        runner = T1Runner(skill_dir=tmp_path / "skill", run=_ok_runner(workdir))
        (tmp_path / "skill").mkdir()
        outcome = runner.run("600519", workdir=workdir)
        assert outcome.ok and (workdir / CONTEXT_PACK_NAME).exists()

    def test_fetch_exit_2_daily_bars_failed(self, tmp_path: Path):
        workdir = tmp_path / "w"
        (tmp_path / "skill").mkdir()
        runner = T1Runner(skill_dir=tmp_path / "skill", run=lambda argv: FakeResult(2))
        outcome = runner.run("600519", workdir=workdir)
        assert not outcome.ok and any("日 K" in r for r in outcome.reasons)

    def test_prepare_nonzero_fails(self, tmp_path: Path):
        def run(argv: list[str]) -> FakeResult:
            return FakeResult(0 if any("fetch.py" in a for a in argv) else 1)

        runner = T1Runner(skill_dir=tmp_path / "skill", run=run)
        assert not runner.run("600519", workdir=tmp_path / "w").ok

    def test_missing_pack_fails_even_exit_zero(self, tmp_path: Path):
        (tmp_path / "skill").mkdir()
        runner = T1Runner(skill_dir=tmp_path / "skill", run=lambda argv: FakeResult(0))
        outcome = runner.run("600519", workdir=tmp_path / "w")
        assert not outcome.ok and any("context-pack" in r for r in outcome.reasons)

    def test_missing_skill_dir_fails_without_running(self, tmp_path: Path):
        def boom(argv):
            raise AssertionError("skill_dir 缺失时不应触达 runner")

        runner = T1Runner(skill_dir=tmp_path / "nope", run=boom)
        outcome = runner.run("600519", workdir=tmp_path / "w")
        assert not outcome.ok and any("SKILL_DIR" in r for r in outcome.reasons)

    def test_timeout_wrapped(self, tmp_path: Path):
        def run(argv: list[str]):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        runner = T1Runner(skill_dir=tmp_path / "skill", run=run)
        (tmp_path / "skill").mkdir()
        outcome = runner.run("600519", workdir=tmp_path / "w")
        assert not outcome.ok and any("超时" in r for r in outcome.reasons)


class TestResolve:
    def test_skill_dir_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        from stock_trading_agent.loop.t1 import resolve_skill_dir

        monkeypatch.setenv("STA_SKILL_DIR", str(tmp_path))
        assert resolve_skill_dir() == tmp_path

    def test_uv_env_override(self, monkeypatch: pytest.MonkeyPatch):
        from stock_trading_agent.loop.t1 import resolve_uv

        monkeypatch.setenv("STA_UV_PATH", "/custom/uv")
        assert resolve_uv() == "/custom/uv"
