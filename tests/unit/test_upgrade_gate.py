"""升级门禁纯函数族（0.0.26）：--help 解析、watched diff 分级、CHANGELOG 窗口、聚合。"""

from __future__ import annotations

from stock_trading_agent.cc_client.upgrade_gate import (
    Finding,
    aggregate,
    diff_snapshots,
    load_snapshot,
    parse_help_commands,
    parse_help_flags,
    scan_changelog,
    serialize_snapshot,
    snapshot_from_capture,
    watch_flags,
)

HELP_FIXTURE = """Usage: claude [options] [command] [prompt]

Options:
  --allowedTools, --allowed-tools <tools...>
      Comma or space-separated list of tool names to allow (e.g. "Bash(git *)")
  --append-system-prompt <prompt>       Append a system prompt to the default
                                        system prompt
  --autocompact <auto|tokens>           Auto-compact window size
  --permission-mode <mode>              Permission mode (choices: "acceptEdits",
                                        "auto", "bypassPermissions", "manual",
                                        "dontAsk", "plan")
  --strict-mcp-config                   Strict MCP
  --system-prompt-snapshot <on|off>     System prompt snapshot (choices: "on", "off")
  --verbose                            Verbose output

Commands:
  agents [options]                      Manage background agents
  attach <id|name>                      Open a background session in this
                                        terminal
  auth                                  Manage authentication
  install <version>                     Install a version
"""


def _snap(text: str, ver: str = "2.1.319", **kw):
    return snapshot_from_capture(ver, "2026-11-08T00:00:00+00:00", text, **kw)


class TestParseHelp:
    def test_alias_line_with_wrap(self):
        flags = parse_help_flags(HELP_FIXTURE)
        f = flags["--allowedTools"]
        assert f.aliases == ("--allowed-tools",) and f.value == "<tools...>"

    def test_choices_multiline(self):
        flags = parse_help_flags(HELP_FIXTURE)
        assert "dontAsk" in flags["--permission-mode"].choices
        assert set(flags["--system-prompt-snapshot"].choices) == {"on", "off"}

    def test_value_placeholder_enum(self):
        assert parse_help_flags(HELP_FIXTURE)["--autocompact"].value == "<auto|tokens>"

    def test_commands_exact_indent(self):
        assert parse_help_commands(HELP_FIXTURE) == ("agents", "attach", "auth", "install")


class TestWatchFlags:
    def test_superset_of_build_argv_flags(self):
        wf = watch_flags()
        # 与 argv_snapshot selftest 同源：在用面非手抄，关键旗标必须齐
        for must in ("--session-id", "--permission-mode", "--allowedTools", "--strict-mcp-config"):
            assert must in wf, must


class TestDiff:
    def test_self_diff_only_hidden_info(self):
        base = _snap(HELP_FIXTURE)
        findings = diff_snapshots(base, base)
        # fixture 未含全部在用旗标 → 多条 hidden info；无 fail/warn，自差恒 pass
        assert findings and all(f.code == "watch_flag_hidden" for f in findings)

    def test_watch_flag_removed_is_fail(self):
        base = _snap(HELP_FIXTURE)
        needle = (
            "  --system-prompt-snapshot <on|off>     "
            'System prompt snapshot (choices: "on", "off")\n'
        )
        broken = HELP_FIXTURE.replace(needle, "")
        cur = _snap(broken, ver="2.1.320")
        findings = diff_snapshots(base, cur)
        assert any(f.code == "watch_flag_removed" and f.severity == "fail" for f in findings)

    def test_non_watch_removed_is_warn(self):
        base = _snap(HELP_FIXTURE)
        needle = (
            "  --append-system-prompt <prompt>       Append a system prompt to the default\n"
            "                                        system prompt\n"
        )
        cur = _snap(HELP_FIXTURE.replace(needle, ""))
        findings = diff_snapshots(base, cur)
        assert any(f.code == "flag_removed" and f.severity == "warn" for f in findings)
        assert not any(f.severity == "fail" for f in findings)

    def test_added_is_info(self):
        base = _snap(HELP_FIXTURE)
        cur = _snap(HELP_FIXTURE + "  --brand-new <x>                       New flag\n")
        findings = diff_snapshots(base, cur)
        assert any(f.code == "flag_added" and f.severity == "info" for f in findings)

    def test_choice_narrowed_is_fail(self):
        base = _snap(HELP_FIXTURE)
        old = (
            '"bypassPermissions", "manual",\n'
            '                                        "dontAsk", "plan")'
        )
        narrowed = HELP_FIXTURE.replace(
            old,
            '"bypassPermissions", "manual",\n                                        "plan")',
        )
        cur = _snap(narrowed, ver="2.1.320")
        findings = diff_snapshots(base, cur)
        hit = [f for f in findings if f.code == "watch_choice_removed"]
        assert hit and hit[0].severity == "fail" and "dontAsk" in hit[0].detail

    def test_renamed_heuristic_warn(self):
        base = _snap(HELP_FIXTURE)
        cur = _snap(
            HELP_FIXTURE.replace("--autocompact <auto|tokens>", "--autocompact2 <auto|tokens>"),
            ver="2.1.320",
        )
        assert any(f.code == "flag_renamed" for f in diff_snapshots(base, cur))

    def test_capability_removed_warn(self):
        base = _snap(HELP_FIXTURE, capabilities={"a": 1})
        cur = _snap(HELP_FIXTURE, capabilities={})
        assert any(f.code == "capability_removed" for f in diff_snapshots(base, cur))


class TestChangelog:
    TEXT = """# Changelog
## 2.1.320
- feat: new thing with --flag X
- fix: session handling
## 2.1.319
- fix: stable stuff
## 2.1.318
- removed: legacy headless flag
"""

    def test_window_extracts_and_hits_keywords(self):
        codes = [f.code for f in scan_changelog(self.TEXT, "2.1.319", "2.1.320")]
        assert "changelog_keyword" in codes  # flag/session 命中于 (2.1.319..2.1.320]

    def test_window_excludes_baseline_version(self):
        # 2.1.319 自身条目（stable stuff）不入窗：无关键词命中时仅空窗/命中由内容定
        findings = scan_changelog(self.TEXT, "2.1.318", "2.1.319")
        assert all("legacy headless" not in f.detail for f in findings)  # 2.1.318 属基线侧不含

    def test_empty_window_warns(self):
        codes = [f.code for f in scan_changelog(self.TEXT, "9.9.9", "9.9.10")]
        assert "changelog_window_empty" in codes


class TestAggregateAndSerialize:
    def test_aggregate_precedence(self):
        assert aggregate([Finding("a", "info", "x")]).verdict == "pass"
        assert aggregate([Finding("a", "info", "x"), Finding("b", "warn", "y")]).verdict == "warn"
        assert aggregate([Finding("a", "warn", "y"), Finding("b", "fail", "z")]).verdict == "fail"
        assert aggregate([]).verdict == "pass"

    def test_roundtrip(self, tmp_path):
        snap = _snap(HELP_FIXTURE, capabilities=None)
        path = tmp_path / "claude-2.1.319.snapshot.json"
        path.write_text(serialize_snapshot(snap), encoding="utf-8")
        back = load_snapshot(path)
        assert back.cli_version == snap.cli_version
        assert back.flags["--permission-mode"].choices == snap.flags["--permission-mode"].choices
        assert back.commands == snap.commands and back.capabilities is None
