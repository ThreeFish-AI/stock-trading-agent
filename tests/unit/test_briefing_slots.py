"""0.0.18 早晚分卡：六层加槽（kind/UUID/dedup/卡路径/ack/面板）+ 自动触发判据。"""

from __future__ import annotations

import datetime as dt
import typing
from pathlib import Path

from stock_trading_agent.loop.briefing import (
    AUTO_EVENING_WINDOW,
    BRIEFING_EVENING,
    BRIEFING_MORNING,
    build_card_md,
    has_ack,
    persist_card,
    plan_auto_briefings,
    scan_briefing_text,
    slot_of_kind,
)
from stock_trading_agent.state.paths import StateLayout

MONDAY_0910 = dt.datetime(2026, 11, 9, 9, 10, tzinfo=dt.timezone(dt.timedelta(hours=8)))
MONDAY_1220 = dt.datetime(2026, 11, 9, 12, 20, tzinfo=dt.timezone(dt.timedelta(hours=8)))
MONDAY_1820 = dt.datetime(2026, 11, 9, 18, 20, tzinfo=dt.timezone(dt.timedelta(hours=8)))
SAT_0910 = dt.datetime(2026, 11, 14, 9, 10, tzinfo=dt.timezone(dt.timedelta(hours=8)))


class TestSlotBasics:
    def test_slot_of_kind(self):
        assert slot_of_kind(BRIEFING_MORNING) == "am"
        assert slot_of_kind(BRIEFING_EVENING) == "pm"
        assert slot_of_kind("briefing_review") == ""
        assert slot_of_kind("diagnose_full") == ""

    def test_build_card_md_slot_frontmatter(self):
        md_am = build_card_md({"briefing": "x"}, date="2026-11-09", code="600519", slot="am")
        assert "session: am" in md_am and "## 盘前晨报 · 600519" in md_am
        md_pm = build_card_md({"briefing": "x"}, date="2026-11-09", code="600519", slot="pm")
        assert "session: pm" in md_pm and "## 盘后复盘 · 600519" in md_pm
        md_legacy = build_card_md({"briefing": "x"}, date="2026-11-09", code="600519")
        assert "session:" not in md_legacy and "盘前晨报 / 盘后复盘" in md_legacy
        assert not scan_briefing_text(md_am)

    def test_persist_card_slot_paths(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path)
        am = persist_card(layout, "2026-11-09", "600519", "# am", slot="am")
        pm = persist_card(layout, "2026-11-09", "600519", "# pm", slot="pm")
        legacy = persist_card(layout, "2026-11-09", "600519", "# l")
        assert am.name == "600519.am.md" and pm.name == "600519.pm.md"
        assert legacy.name == "600519.md"  # 三卡并存不互覆

    def test_has_ack_slot_matching(self):
        lines = [
            {
                "kind": "briefing_ack",
                "payload": {"date": "2026-11-09", "code": "600519", "session": "am"},
            },
            {"kind": "briefing_ack", "payload": {"date": "2026-11-09", "code": "000001"}},
        ]
        assert has_ack(lines, date="2026-11-09", code="600519", slot="am")
        assert not has_ack(lines, date="2026-11-09", code="600519", slot="pm")  # 槽位隔离
        assert not has_ack(lines, date="2026-11-09", code="600519")  # legacy 卡不被 am ack 覆盖
        assert has_ack(lines, date="2026-11-09", code="000001")  # legacy ack 命中 legacy 卡
        assert not has_ack(lines, date="2026-11-09", code="000001", slot="am")


class TestPlanAutoBriefings:
    CODES: typing.ClassVar[list[str]] = ["000001", "600519"]

    def test_l2_off_or_universe_missing_empty(self):
        assert (
            plan_auto_briefings(
                MONDAY_0910, l2_enabled=False, universe_codes=self.CODES, already_queued=set()
            )
            == []
        )
        assert (
            plan_auto_briefings(
                MONDAY_0910, l2_enabled=True, universe_codes=None, already_queued=set()
            )
            == []
        )

    def test_weekend_blocked(self):
        assert (
            plan_auto_briefings(
                SAT_0910, l2_enabled=True, universe_codes=self.CODES, already_queued=set()
            )
            == []
        )

    def test_morning_and_evening_windows(self):
        planned = plan_auto_briefings(
            MONDAY_0910, l2_enabled=True, universe_codes=self.CODES, already_queued=set()
        )
        assert planned == [(BRIEFING_MORNING, "000001"), (BRIEFING_MORNING, "600519")]
        planned = plan_auto_briefings(
            MONDAY_1820, l2_enabled=True, universe_codes=self.CODES, already_queued=set()
        )
        assert planned == [(BRIEFING_EVENING, "000001"), (BRIEFING_EVENING, "600519")]

    def test_outside_windows_empty(self):
        assert (
            plan_auto_briefings(
                MONDAY_1220, l2_enabled=True, universe_codes=self.CODES, already_queued=set()
            )
            == []
        )

    def test_dedup_filters_per_code_per_slot(self):
        queued = {f"{BRIEFING_MORNING}:600519:2026-11-09"}
        planned = plan_auto_briefings(
            MONDAY_0910, l2_enabled=True, universe_codes=self.CODES, already_queued=queued
        )
        assert planned == [(BRIEFING_MORNING, "000001")]  # 已入队 code 跳过；pm 槽不受 am 影响
        again = plan_auto_briefings(
            MONDAY_1820, l2_enabled=True, universe_codes=self.CODES, already_queued=queued
        )
        assert (BRIEFING_EVENING, "600519") in again

    def test_evening_window_after_no_report_window(self):
        (sh, sm), _ = AUTO_EVENING_WINDOW
        assert (sh * 60 + sm) > (18 * 60 + 10)  # 盘后窗在禁跑窗之后（18:10 含端点）


class TestRoutingAndGates:
    def test_new_kinds_routable_with_nr4(self):
        from stock_trading_agent.loop.router import load_default_table

        table = load_default_table()
        for kind in (BRIEFING_MORNING, BRIEFING_EVENING):
            tpl = table.route(kind)
            assert tpl is not None
            assert tpl.budget_usd > 0 and tpl.max_turns > 0  # NR-4 字段在场
            assert tpl.schema_ref == "briefing"

    def test_no_report_window_covers_new_kinds(self):
        from stock_trading_agent.loop.events import gate_report_event

        in_window = dt.datetime(2026, 11, 9, 17, 40, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        for kind in (BRIEFING_MORNING, BRIEFING_EVENING):
            assert gate_report_event(in_window, kind) is not None

    def test_submit_whitelist_accepts_new_kinds(self):
        from stock_trading_agent.server.schemas import SubmitRequest

        req = SubmitRequest(kind=BRIEFING_MORNING, code="600519")
        assert req.kind == BRIEFING_MORNING
