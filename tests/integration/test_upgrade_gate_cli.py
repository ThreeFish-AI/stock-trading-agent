"""升级门禁 CLI 端到端（0.0.26）：fake claude PATH 注入 + 黑盒 main([...])。

覆盖：PASS 全链（决策行落 tmp 链）/ 在用旗标消失 FAIL / 非法枚举吃进 FAIL /
--capture 落快照 / 基线缺席拒跑。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from stock_trading_agent.__main__ import main

HELP = """Usage: claude [options] [command] [prompt]

Options:
  --allowedTools, --allowed-tools <tools...>
      Comma or space-separated list
  --mcp-config <file>                    MCP config file
  --output-format <format>               Output format (choices: "text", "json",
                                        "stream-json")
  --permission-mode <mode>               Permission mode (choices: "acceptEdits",
                                        "auto", "bypassPermissions", "manual",
                                        "dontAsk", "plan")
  --permission-prompts <behavior>        Permission prompts behavior
  --session-id <uuid>                    Session id
  --setting-sources <sources...>         Setting sources
  --strict-mcp-config                    Strict MCP config
  --system-prompt-snapshot <on|off>      System prompt snapshot (choices: "on", "off")
  --verbose                              Verbose output

Commands:
  auth                                   Manage authentication
  install <version>                      Install a version
"""


def _make_fake_claude(bin_dir: Path, *, version: str, help_text: str, enum_rejects: bool = True):
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    help_file = bin_dir / "help.txt"
    help_file.write_text(help_text, encoding="utf-8")
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n'
        'if [ "$1" = "--help" ]; then cat "$(dirname "$0")/help.txt"; exit 0; fi\n'
        f'if [ "$1" = "--permission-mode" ]; then exit {1 if enum_rejects else 0}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _seed_baseline(out_dir: Path, help_text: str, ver: str = "2.1.319") -> None:
    from stock_trading_agent.cc_client.upgrade_gate import (
        serialize_snapshot,
        snapshot_from_capture,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    snap = snapshot_from_capture(ver, "2026-11-08T00:00:00+00:00", help_text)
    (out_dir / f"claude-{ver}.snapshot.json").write_text(serialize_snapshot(snap), encoding="utf-8")
    (out_dir / "pinned.json").write_text(json.dumps({"cli_version": ver}), encoding="utf-8")


class TestUpgradeGateCLI:
    def test_pass_full_chain(self, tmp_path: Path, monkeypatch):
        bin_dir, out_dir, state = tmp_path / "bin", tmp_path / "gate", tmp_path / "state"
        _make_fake_claude(bin_dir, version="2.1.320 (Claude Code)", help_text=HELP)
        _seed_baseline(out_dir, HELP)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")
        monkeypatch.setenv("STA_STATE_ROOT", str(state))
        rc = main(["upgrade-gate", "--out", str(out_dir)])
        assert rc == 0
        lines = [
            json.loads(x)
            for x in (state / "agent" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if x
        ]
        runs = [d for d in lines if d["kind"] == "upgrade_gate_run"]
        assert runs and runs[-1]["payload"]["verdict"] == "pass"
        assert runs[-1]["payload"]["baseline_version"] == "2.1.319"
        assert runs[-1]["payload"]["current_version"] == "2.1.320"

    def test_watch_flag_removed_fails(self, tmp_path: Path, monkeypatch):
        bin_dir, out_dir, state = tmp_path / "bin", tmp_path / "gate", tmp_path / "state"
        needle = (
            "  --system-prompt-snapshot <on|off>      "
            'System prompt snapshot (choices: "on", "off")\n'
        )
        broken = HELP.replace(needle, "")
        _make_fake_claude(bin_dir, version="2.1.320 (Claude Code)", help_text=broken)
        _seed_baseline(out_dir, HELP)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")
        monkeypatch.setenv("STA_STATE_ROOT", str(state))
        rc = main(["upgrade-gate", "--out", str(out_dir)])
        assert rc == 1
        lines = [
            json.loads(x)
            for x in (state / "agent" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if x
        ]
        last = [d for d in lines if d["kind"] == "upgrade_gate_run"][-1]
        assert last["payload"]["verdict"] == "fail"
        assert "watch_flag_removed" in last["payload"]["codes"]

    def test_enum_eaten_fails(self, tmp_path: Path, monkeypatch):
        bin_dir, out_dir, state = tmp_path / "bin", tmp_path / "gate", tmp_path / "state"
        _make_fake_claude(
            bin_dir, version="2.1.320 (Claude Code)", help_text=HELP, enum_rejects=False
        )
        _seed_baseline(out_dir, HELP)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")
        monkeypatch.setenv("STA_STATE_ROOT", str(state))
        rc = main(["upgrade-gate", "--out", str(out_dir)])
        assert rc == 1  # 隐藏别名吃进非法值（F-30）→ FAIL

    def test_capture_writes_snapshot(self, tmp_path: Path, monkeypatch):
        bin_dir, out_dir, state = tmp_path / "bin", tmp_path / "gate", tmp_path / "state"
        _make_fake_claude(bin_dir, version="2.1.320 (Claude Code)", help_text=HELP)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")
        monkeypatch.setenv("STA_STATE_ROOT", str(state))
        assert main(["upgrade-gate", "--capture", "--out", str(out_dir)]) == 0
        snap = out_dir / "claude-2.1.320.snapshot.json"
        assert snap.exists() and (out_dir / "claude-2.1.320.help.txt").exists()
        raw = json.loads(snap.read_text(encoding="utf-8"))
        assert raw["cli_version"] == "2.1.320"
        assert "--permission-mode" in raw["flags"]

    def test_missing_baseline_refuses(self, tmp_path: Path, monkeypatch):
        bin_dir, out_dir, state = tmp_path / "bin", tmp_path / "gate", tmp_path / "state"
        _make_fake_claude(bin_dir, version="2.1.320 (Claude Code)", help_text=HELP)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")
        monkeypatch.setenv("STA_STATE_ROOT", str(state))
        assert main(["upgrade-gate", "--out", str(out_dir)]) == 1  # pinned.json 缺席
