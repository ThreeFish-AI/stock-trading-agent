"""paper EOD 驱动纯函数：ticks 末轮 bar 推导 + 触发判定矩阵 + 幂等探针。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from stock_trading_agent.loop.paper_eod import (
    EOD_WINDOW_START,
    derive_day_bars,
    eod_ran_on,
    plan_paper_eod,
)

TZ8 = dt.timezone(dt.timedelta(hours=8))


def _tick(code: str, ts: str, **over) -> dict:
    row = {
        "code": code,
        "market": "cn",
        "price": 10.0,
        "open": 9.9,
        "high": 10.2,
        "low": 9.8,
        "prev_close": 9.95,
        "ts_utc8": ts,
    }
    row.update(over)
    return row


class TestDeriveDayBars:
    def test_last_tick_wins_and_ohlc_accumulates(self, tmp_path: Path):
        f = tmp_path / "2026-11-10.jsonl"
        f.write_text(
            json.dumps(_tick("600519", "2026-11-10T09:45:00+08:00", high=10.1))
            + "\n"
            + json.dumps(_tick("600519", "2026-11-10T14:57:00+08:00", high=10.4, price=10.3))
            + "\n",
            encoding="utf-8",
        )
        bars = derive_day_bars(f, ["600519"])
        assert bars["600519"].close == 10.3  # 末轮 price
        assert bars["600519"].high == 10.4  # 末轮 high=全日累计（bar 源口径 §⑧）
        assert bars["600519"].date == "2026-11-10"
        assert bars["600519"].prev_close == 9.95

    def test_bad_lines_skipped_and_absent_codes_dropped(self, tmp_path: Path):
        f = tmp_path / "d.jsonl"
        f.write_text(
            "{broken\n"
            + json.dumps(_tick("600519", "2026-11-10T14:57:00+08:00"))
            + "\n"
            + json.dumps(_tick("00700", "2026-11-10T15:57:00+08:00"))
            + "\n"
            + json.dumps(_tick("300624", "2026-11-10T14:57:00+08:00", open=None))
            + "\n",  # 字段漂移坏行：宁缺毋猜
            encoding="utf-8",
        )
        bars = derive_day_bars(f, ["600519", "00700", "300624", "999999"])
        assert set(bars) == {"600519", "00700"}

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert derive_day_bars(tmp_path / "nope.jsonl", ["600519"]) == {}


class TestPlanPaperEod:
    def test_matrix(self):
        in_window = dt.datetime(2026, 11, 10, 16, 6, tzinfo=TZ8)
        assert EOD_WINDOW_START == dt.time(16, 5)
        assert (
            plan_paper_eod(in_window, has_portfolio=True, ticks_exists=True, already_ran=False)
            is None
        )
        assert (
            plan_paper_eod(in_window, has_portfolio=True, ticks_exists=False, already_ran=False)
            == "no_ticks"
        )
        assert (
            plan_paper_eod(in_window, has_portfolio=True, ticks_exists=True, already_ran=True)
            == "already_ran"
        )
        assert (
            plan_paper_eod(in_window, has_portfolio=False, ticks_exists=True, already_ran=False)
            == "no_portfolio"
        )
        morning = dt.datetime(2026, 11, 10, 10, 0, tzinfo=TZ8)
        assert (
            plan_paper_eod(morning, has_portfolio=True, ticks_exists=True, already_ran=False)
            == "before_window"
        )


class TestEodRanOn:
    def test_tolerant_scan(self, tmp_path: Path):
        f = tmp_path / "decisions.jsonl"
        f.write_text(
            '{"kind":"task_submitted","payload":{"date":"2026-11-10"}}\n'
            "{broken\n"
            + json.dumps({"kind": "paper_eod_done", "payload": {"date": "2026-11-09", "bars": 3}})
            + "\n",
            encoding="utf-8",
        )
        assert eod_ran_on(f, "2026-11-09") is True
        assert eod_ran_on(f, "2026-11-10") is False
        assert eod_ran_on(tmp_path / "absent.jsonl", "2026-11-10") is False

    def test_skipped_counts_as_ran(self, tmp_path: Path):
        f = tmp_path / "decisions.jsonl"
        f.write_text(
            json.dumps({"kind": "paper_eod_skipped", "payload": {"date": "2026-11-10"}}) + "\n",
            encoding="utf-8",
        )
        assert eod_ran_on(f, "2026-11-10") is True
