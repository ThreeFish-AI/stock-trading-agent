"""DryRunCCClient：合成信封可过三段判据、审计归档路径跑通。"""

from __future__ import annotations

from pathlib import Path

from stock_trading_agent.cc_client.contract import derive_session_uuid
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.assertor import assess_result
from stock_trading_agent.loop.events import EventEnvelope, dedup_key


def make_client(audit_dir: Path | None = None) -> tuple[DryRunCCClient, object]:
    return DryRunCCClient(audit_dir=audit_dir), None


class TestDryRun:
    def test_spawn_passes_assertor(self, tmp_path: Path):
        client, _ = make_client(tmp_path / "audit")
        from stock_trading_agent.cc_client.contract import CCRequest

        req = CCRequest(
            task_id="diagnose_quick:600519:1",
            session_uuid=derive_session_uuid("2026-11-07", "600519", "diagnose_quick"),
            prompt="/vibe-stock 600519",
            schema=None,
            budget_usd=2.0,
            max_turns=20,
            allowed_tools=("Read",),
        )
        result = client.spawn_once(req)
        assert result.exit_code == 0 and result.envelope is not None and result.result_line_present
        assert assess_result(result, "diagnose_quick").ok

    def test_audit_archive_written(self, tmp_path: Path):
        audit = tmp_path / "audit"
        client, _ = make_client(audit)
        from stock_trading_agent.cc_client.contract import CCRequest

        req = CCRequest(
            task_id="diagnose_quick:600519:2",
            session_uuid=derive_session_uuid("d", "c", "k"),
            prompt="p",
            schema=None,
            budget_usd=1.0,
            max_turns=5,
            allowed_tools=("Read",),
        )
        client.spawn_once(req)
        files = list(audit.rglob("dryrun-*.jsonl"))
        assert not files  # NR-3 次序：spawn 期不落盘（断言器放行前无归档）
        client.archive(req)  # Loop 在断言器放行后调用
        files = list(audit.rglob("dryrun-*.jsonl"))
        assert files and files[0].read_text(encoding="utf-8").strip().endswith("}")

    def test_probe_is_dryrun(self):
        client, _ = make_client()
        report = client.probe()
        assert report.auth_mode == "dry-run" and report.cli_version == "dry-run"

    def test_resume_keeps_session(self):
        client, _ = make_client()
        from stock_trading_agent.cc_client.contract import CCRequest

        req = CCRequest(
            task_id="t",
            session_uuid="u",
            prompt="p",
            schema=None,
            budget_usd=1.0,
            max_turns=5,
            allowed_tools=("Read",),
        )
        result = client.resume("sess-1", req)
        assert result.envelope["session_id"] == "sess-1"


class TestDedupKey:
    def test_shape(self):
        ev = EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519")
        assert dedup_key(ev, "2026-11-07") == "diagnose_quick:600519:2026-11-07"
