"""严格穿价撮合与边界（口径：vibe-stock references/07 成交/提示两把尺；06 Stage D 跳空回落）。"""

from __future__ import annotations

import pytest

from stock_trading_agent.paper.fills import (
    Bar,
    Fill,
    match_bar_pessimistic,
    match_limit_buy,
    match_limit_sell,
    match_stop,
    t1_gap_fallback_limit,
    t1_gap_needs_fallback,
)

BAR = Bar(date="2026-11-07", open=10.0, high=10.6, low=9.5, close=9.9)


class TestLimitBuy:
    def test_strict_cross_fills_at_min_open_limit(self):
        fill = match_limit_buy("600519", 2, 9.6, BAR)
        assert fill is not None
        assert fill.price == pytest.approx(9.6) and fill.side == "buy" and fill.lots == 2

    def test_open_capped_when_limit_above_open(self):
        fill = match_limit_buy("600519", 1, 99.0, BAR)
        assert fill is not None and fill.price == pytest.approx(10.0)  # 价 = min(open, limit)

    def test_low_equal_limit_no_fill(self):
        # 两把尺：提示口径 low ≤ entry_ref 只推演；成交判据严格穿价 low < limit
        assert match_limit_buy("600519", 1, 9.5, BAR) is None

    def test_low_above_limit_no_fill(self):
        assert match_limit_buy("600519", 1, 9.0, BAR) is None

    def test_tick_knob_requires_one_tick(self):
        just_under = Bar(date="d", open=10.0, high=10.0, low=9.599, close=9.6)
        assert match_limit_buy("600519", 1, 9.6, just_under) is None  # 穿价不足 1 tick


class TestLimitSellMirror:
    def test_strict_cross_price_max_open_limit(self):
        fill = match_limit_sell("600519", 1, 10.5, BAR)
        assert fill is not None and fill.price == pytest.approx(10.5)  # 价 = max(open, limit)

    def test_gap_up_open_fills_at_open(self):
        # 卖出镜像：open 10.0 > limit 9.8 且 high 穿价 → 价 = max(open, limit) = open
        fill = match_limit_sell("600519", 1, 9.8, BAR)
        assert fill is not None and fill.price == pytest.approx(10.0)

    def test_no_cross_no_fill(self):
        assert match_limit_sell("600519", 1, 10.7, BAR) is None


class TestStop:
    def test_intraday_break_fills_at_stop_minus_slippage(self):
        fill = match_stop("600519", 1, 9.7, BAR, slippage=0.01)
        assert fill is not None and fill.price == pytest.approx(9.69)

    def test_open_gap_fills_at_open(self):
        gap = Bar(date="d", open=9.4, high=9.5, low=9.3, close=9.4)
        fill = match_stop("600519", 1, 9.7, gap, slippage=0.01)
        assert fill is not None and fill.price == pytest.approx(9.39)  # 开盘更差价

    def test_no_trigger_none(self):
        quiet = Bar(date="d", open=10.0, high=10.2, low=9.9, close=10.1)
        assert match_stop("600519", 1, 9.8, quiet) is None


class TestPessimisticOrder:
    def test_stop_wins_when_both_trigger(self):
        fill = match_bar_pessimistic("600519", 1, limit=10.4, stop=9.6, bar=BAR)
        assert fill.tranche_leg == "stop"

    def test_limit_when_stop_not_hit(self):
        quiet = Bar(date="d", open=10.0, high=10.5, low=9.95, close=10.4)
        fill = match_bar_pessimistic("600519", 1, limit=10.4, stop=9.6, bar=quiet)
        assert fill.tranche_leg == "limit"

    def test_no_trigger_raises(self):
        quiet = Bar(date="d", open=10.0, high=10.1, low=9.95, close=10.05)
        with pytest.raises(ValueError):
            match_bar_pessimistic("600519", 1, limit=10.4, stop=9.6, bar=quiet)


class TestT1GapFallback:
    def test_limit_is_close_times_102(self):
        assert t1_gap_fallback_limit(10.0) == pytest.approx(10.2)
        assert t1_gap_fallback_limit(26.0) == pytest.approx(26.52)

    def test_needs_fallback_strict(self):
        assert t1_gap_needs_fallback(10.0, next_open=10.3) is True  # > +2% 转限价
        assert t1_gap_needs_fallback(10.0, next_open=10.2) is False  # 等于不转（> 严格）
        assert t1_gap_needs_fallback(10.0, next_open=10.0) is False

    def test_fill_shape(self):
        f = Fill(code="600519", side="buy", lots=1, price=10.0, date="d", tranche_leg="T1_open")
        assert (f.code, f.side, f.lots) == ("600519", "buy", 1)
