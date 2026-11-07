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
    """禁跑窗按市场本地钟面（Asia/Shanghai）判定：UTC 传入也须正确换算（防 8 小时错位）。"""

    def test_window_bounds_local_clock(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        ev_kind = "briefing_review"
        assert gate_report_event(dt.datetime(2026, 11, 7, 17, 30, tzinfo=tz), ev_kind) is not None
        assert gate_report_event(dt.datetime(2026, 11, 7, 18, 10, tzinfo=tz), ev_kind) is not None
        assert gate_report_event(dt.datetime(2026, 11, 7, 18, 11, tzinfo=tz), ev_kind) is None
        assert gate_report_event(dt.datetime(2026, 11, 7, 10, 0, tzinfo=tz), ev_kind) is None

    def test_utc_input_converted_to_market_clock(self):
        # 北京 17:45（真实危险窗）= UTC 09:45——UTC 钟面不在窗内但换算后必须拦截
        assert (
            gate_report_event(dt.datetime(2026, 11, 7, 9, 45, tzinfo=dt.UTC), "diagnose_full")
            is not None
        )
        # UTC 17:45 = 北京 01:45（无槽位）——必须放行（旧实现在此误拦）
        assert (
            gate_report_event(dt.datetime(2026, 11, 7, 17, 45, tzinfo=dt.UTC), "diagnose_full")
            is None
        )

    def test_blocking_kinds_cover_signal_writers(self):
        # 写 signals.json 的全诊/批量类必须在禁跑窗内被拦（旧实现只拦 report* 前缀全部落空）
        from stock_trading_agent.loop.events import REPORT_BLOCKING_KINDS

        assert {"diagnose_full", "diagnose_quick", "batch_wave"} <= REPORT_BLOCKING_KINDS
        in_window = dt.datetime(2026, 11, 7, 17, 45, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        for kind in ("diagnose_full", "diagnose_quick", "batch_wave", "briefing_review"):
            assert gate_report_event(in_window, kind) is not None, kind

    def test_non_report_kinds_ungated(self):
        in_window = dt.datetime(2026, 11, 7, 17, 45, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        assert gate_report_event(in_window, "monitor_review") is None
        assert gate_report_event(in_window, "paper_confirm") is None

    def test_lock_precheck(self):
        assert lock_precheck(fetch_lock=True, watch_lock=False) is not None
        assert lock_precheck(fetch_lock=False, watch_lock=True) is not None
        assert lock_precheck(fetch_lock=False, watch_lock=False) is None
