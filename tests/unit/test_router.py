"""YAML 确定性路由表与禁跑窗/锁前置（零 LLM 分派占位）。"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_trading_agent.loop.events import gate_report_event, lock_precheck
from stock_trading_agent.loop.router import RoutingError, load_default_table, parse_table

EXPECTED_KINDS = {
    "diagnose_full",
    "diagnose_quick",
    "monitor_review",
    "batch_wave",
    "paper_confirm",
    "briefing_review",
}


class TestRoutingTable:
    def test_six_kinds_hit_templates(self):
        table = load_default_table()
        assert EXPECTED_KINDS <= set(table.routes)
        for kind in EXPECTED_KINDS:
            template = table.route(kind)
            assert template is not None
            assert template.budget_usd > 0 and template.max_turns > 0  # NR-4
            assert "{code}" in template.prompt_template

    def test_unknown_kind_falls_back_and_counts(self):
        table = load_default_table()
        assert table.route("no_such_kind") is None
        assert table.route("no_such_kind") is None
        assert table.fallback_count == 2

    def test_zero_llm_dispatch_entries(self):
        table = load_default_table()
        assert table.zero_llm["t0_event"] == "local_notify"
        assert table.zero_llm["t1_refresh"] == "diagnose_prepare"

    def test_nr4_violating_table_rejected(self):
        with pytest.raises(RoutingError):
            parse_table(
                {"routes": [{"kind": "x", "prompt_template": "p", "budget_usd": 0, "max_turns": 5}]}
            )
        with pytest.raises(RoutingError):
            parse_table(
                {"routes": [{"kind": "x", "prompt_template": "p", "budget_usd": 1, "max_turns": 0}]}
            )

    def test_sentinel_budget_present(self):
        table = load_default_table()
        assert table.defaults["sentinel_budget_usd"] == 1000.0  # D2① 高位哨兵（NR-4 旗标在场性）


class TestWindows:
    def test_no_report_window_bounds(self):
        ev_kind = "briefing_review"
        assert (
            gate_report_event(dt.datetime(2026, 11, 7, 17, 30, tzinfo=dt.UTC), ev_kind) is not None
        )
        assert (
            gate_report_event(dt.datetime(2026, 11, 7, 18, 10, tzinfo=dt.UTC), ev_kind) is not None
        )
        assert gate_report_event(dt.datetime(2026, 11, 7, 18, 11, tzinfo=dt.UTC), ev_kind) is None
        assert gate_report_event(dt.datetime(2026, 11, 7, 10, 0, tzinfo=dt.UTC), ev_kind) is None

    def test_non_report_kinds_ungated(self):
        assert (
            gate_report_event(dt.datetime(2026, 11, 7, 17, 45, tzinfo=dt.UTC), "diagnose_quick")
            is None
        )

    def test_lock_precheck(self):
        assert lock_precheck(fetch_lock=True, watch_lock=False) is not None
        assert lock_precheck(fetch_lock=False, watch_lock=True) is not None
        assert lock_precheck(fetch_lock=False, watch_lock=False) is None
