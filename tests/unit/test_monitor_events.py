"""monitor 台账解析与 T1 触发判据（纯函数；判据只收严）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_trading_agent.loop.monitor_events import (
    MonitorEvent,
    market_today,
    parse_line,
    read_new_lines,
    t1_trigger,
)

GOOD = (
    '{"ts":"09:31:00","code":"600519","market":"cn","kind":"real","rule":"stop_break",'
    '"level":"T0","notify":true,"mech_risk":{"downgrade":true,"severity":"high"},'
    '"facts":{"pct":-2.1},"review":"止损复核"}'
)
SUPPRESSED = (
    '{"ts":"09:32:00","code":"600519","market":"cn","kind":"real","rule":"band_lo_touch",'
    '"level":"T0","notify":false,"facts":{},"review":"低吸复核","suppress_reason":"cooldown"}'
)
MARKET_LEVEL = (
    '{"ts":"09:33:00","code":"*","market":"cn","kind":"*","rule":"storm_digest",'
    '"level":"T0","notify":false,"facts":{}}'
)
PLAIN = (
    '{"ts":"09:34:00","code":"00700","market":"hk","kind":"sim","rule":"target_hit",'
    '"level":"T0","notify":true,"mech_risk":null,"facts":{"pct":2.3},"review":"止盈复核"}'
)


class TestParseLine:
    def test_full_row_maps_all_fields(self):
        ev = parse_line(GOOD)
        assert ev is not None
        assert ev.code == "600519" and ev.rule == "stop_break" and ev.level == "T0"
        assert ev.mech_risk == {"downgrade": True, "severity": "high"}
        assert ev.review == "止损复核" and ev.suppress_reason is None

    def test_suppressed_row_parses_with_reason(self):
        ev = parse_line(SUPPRESSED)
        assert ev is not None and ev.suppress_reason == "cooldown" and ev.review == "低吸复核"

    def test_market_level_row_parses(self):
        ev = parse_line(MARKET_LEVEL)
        assert ev is not None and ev.code == "*" and ev.mech_risk is None

    def test_bad_lines_return_none(self):
        assert parse_line("{截断") is None
        assert parse_line("") is None
        assert parse_line("   ") is None
        assert parse_line('"just a string"') is None
        assert parse_line('{"ts":"x"}') is None  # 缺必需字段 code/market


class TestT1Trigger:
    def _ev(self, **kw) -> MonitorEvent:
        base = dict(
            ts="t",
            code="600519",
            market="cn",
            kind="real",
            rule="r",
            level="T0",
            notify=True,
        )
        base.update(kw)
        return MonitorEvent(**base)

    def test_review_phrase_triggers(self):
        assert t1_trigger(self._ev(review="止损复核"))

    def test_mech_risk_high_or_medium_triggers(self):
        assert t1_trigger(self._ev(mech_risk={"severity": "high"}))
        assert t1_trigger(self._ev(mech_risk={"severity": "medium"}))

    def test_no_review_no_mech_risk_no_trigger(self):
        assert not t1_trigger(self._ev())
        assert not t1_trigger(self._ev(mech_risk={"severity": "low"}))

    def test_suppressed_never_triggers(self):
        assert not t1_trigger(self._ev(review="止损复核", suppress_reason="stock_daily_cap"))

    def test_market_level_never_triggers(self):
        assert not t1_trigger(self._ev(code="*", review="x"))

    def test_non_t0_level_never_triggers(self):
        assert not t1_trigger(self._ev(level="T1", review="x"))


class TestMarketToday:
    def test_utc_evening_is_next_market_day(self):
        # UTC 20:00 = 上海次日 04:00 → 文件名日期取次日（钟面换算防 8 小时错位）
        assert market_today(dt.datetime(2026, 11, 8, 20, 0, tzinfo=dt.UTC)) == "2026-11-09"

    def test_utc_morning_same_day(self):
        assert market_today(dt.datetime(2026, 11, 8, 1, 0, tzinfo=dt.UTC)) == "2026-11-08"


class TestReadNewLines:
    def test_incremental_reads_and_half_line_cached(self, tmp_path: Path):
        f = tmp_path / "2026-11-08.jsonl"
        f.write_text(GOOD + "\n" + SUPPRESSED + "\n", encoding="utf-8")
        offsets: dict[str, int] = {}
        first = read_new_lines(f, offsets)
        assert len(first) == 2
        expected_bytes = len((GOOD + "\n").encode()) + len((SUPPRESSED + "\n").encode())
        assert offsets["2026-11-08.jsonl"] == expected_bytes  # 偏移按字节（UTF-8 中文 3 字节）

        f.open("a", encoding="utf-8").write(MARKET_LEVEL + "\n" + PLAIN)  # 第 4 行半行（无换行）
        second = read_new_lines(f, offsets)
        assert len(second) == 1 and second[0].code == "*"  # 只消费完整行
        # 半行不越过最后 \n
        assert offsets["2026-11-08.jsonl"] < f.stat().st_size

        f.open("a", encoding="utf-8").write("\n")  # 补上换行
        third = read_new_lines(f, offsets)
        assert len(third) == 1 and third[0].code == "00700"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert read_new_lines(tmp_path / "none.jsonl", {}) == []

    def test_bad_lines_skipped_not_raised(self, tmp_path: Path):
        f = tmp_path / "d.jsonl"
        f.write_text("{bad\n" + GOOD + "\n", encoding="utf-8")
        events = read_new_lines(f, {})
        assert len(events) == 1 and events[0].rule == "stop_break"
