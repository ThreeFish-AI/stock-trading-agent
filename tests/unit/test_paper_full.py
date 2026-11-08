"""paper 全量纯函数族：T+1、涨跌停三态、停牌、整手、公司行动、引擎、确认队列。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_trading_agent.paper.confirm import request_from_fill
from stock_trading_agent.paper.corporate_actions import ESTIMATED as CA_ESTIMATED
from stock_trading_agent.paper.corporate_actions import (
    DividendAction,
    SplitAction,
    apply_corporate_action,
    apply_dividend,
    apply_split,
    dividend_tax_rate,
    fifo_lots,
)
from stock_trading_agent.paper.engine import (
    DayInput,
    evaluate_day,
    mark_equity,
    request_from_t2_event,
)
from stock_trading_agent.paper.fills import Bar, Fill
from stock_trading_agent.paper.ledger import open_position
from stock_trading_agent.paper.model import Operation, SimPortfolio, Tranche
from stock_trading_agent.paper.pending import PendingQueue
from stock_trading_agent.paper.pending import load as pload
from stock_trading_agent.paper.pending import save as psave
from stock_trading_agent.paper.rules import (
    ESTIMATED as RULES_ESTIMATED,
)
from stock_trading_agent.paper.rules import (
    assert_round_lot,
    band_state,
    gate_fill,
    hk_close_review_match,
    limit_band,
)
from stock_trading_agent.paper.tplus1 import available_lots, same_day_bought_lots


def _portfolio(lots: int = 0, tranches: list[Tranche] | None = None) -> SimPortfolio:
    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = open_position(pf, "600519", "A", tranches or [], entry_ref=10.0)
    if lots:
        pf.position("600519").lots = lots
    return pf


def _buy_op(code: str, lots: int, trade_date: str) -> Operation:
    return Operation(
        code=code,
        side="buy",
        lots=lots,
        price=10.0,
        ts=f"{trade_date}T02:00:00+00:00",
        trade_date=trade_date,
    )


class TestTPlus1:
    def test_available_full_next_day(self):
        pf = _portfolio()
        pf.position("600519").lots = 3
        pf.operations = [_buy_op("600519", 1, "2026-11-07")]
        assert available_lots(pf, "600519", "2026-11-08") == 3  # 次日全可卖（日切清零）

    def test_same_day_buy_deducted(self):
        pf = _portfolio()
        pf.position("600519").lots = 3
        pf.operations = [_buy_op("600519", 2, "2026-11-08"), _buy_op("600519", 1, "2026-11-07")]
        assert available_lots(pf, "600519", "2026-11-08") == 1
        assert same_day_bought_lots(pf.operations, "600519", "2026-11-08") == 2

    def test_pending_sell_frozen(self):
        pf = _portfolio()
        pf.position("600519").lots = 3
        assert available_lots(pf, "600519", "2026-11-08", pending_sell_lots=2) == 1

    def test_legacy_op_falls_back_to_ts_market_day(self):
        op = Operation(
            code="600519", side="buy", lots=1, price=10.0, ts="2026-11-07T17:30:00+00:00"
        )
        # UTC 17:30 = 上海 11-08 01:30 → 旧行（无 trade_date）按钟面日归 11-08
        assert same_day_bought_lots([op], "600519", "2026-11-08") == 1


class TestLimitBand:
    def test_rounding_inward(self):
        band = limit_band(9.99)  # 10.989 → 涨停 10.98（向下取整进板）；8.991 → 9.00（向上）
        assert band.upper == pytest.approx(10.98)
        assert band.lower == pytest.approx(9.00)

    def test_band_states(self):
        band = limit_band(10.0)  # 11.00 / 9.00
        assert band_state(Bar("d", 10.2, 10.5, 9.8, 10.1, prev_close=10.0), band) == "normal"
        assert band_state(Bar("d", 11.0, 11.0, 10.5, 11.0, prev_close=10.0), band) == "touched_up"
        assert band_state(Bar("d", 11.0, 11.0, 11.0, 11.0, prev_close=10.0), band) == "locked_up"
        assert band_state(Bar("d", 9.0, 9.0, 9.0, 9.0, prev_close=10.0), band) == "locked_down"
        assert band_state(Bar("d", 9.0, 9.5, 9.0, 9.2, prev_close=10.0), band) == "touched_down"

    def test_gate_one_word_board_blocks_adverse(self):
        band = limit_band(10.0)
        locked = Bar("d", 11.0, 11.0, 11.0, 11.0, prev_close=10.0)
        buy = Fill(code="600519", side="buy", lots=1, price=11.0, date="d")
        assert gate_fill(buy, locked, band) is None  # 一字涨停买入不成交
        sell = Fill(code="600519", side="sell", lots=1, price=11.0, date="d")
        assert gate_fill(sell, locked, band) is not None  # 一字涨停卖出可成交

    def test_gate_touch_clamps_into_band(self):
        band = limit_band(10.0)
        touched = Bar("d", 11.0, 11.0, 10.4, 10.9, prev_close=10.0)
        buy = Fill(code="600519", side="buy", lots=1, price=11.05, date="d")
        gated = gate_fill(buy, touched, band)
        assert gated is not None and gated.price == pytest.approx(11.0)  # clamp 进板

    def test_gate_suspended_blocks_all(self):
        band = limit_band(10.0)
        halted = Bar("d", 10.0, 10.0, 10.0, 10.0, prev_close=10.0, suspended=True)
        buy = Fill(code="600519", side="buy", lots=1, price=10.0, date="d")
        assert gate_fill(buy, halted, band) is None  # 停牌禁买卖

    def test_gate_no_band_passthrough(self):
        buy = Fill(code="00700", side="buy", lots=1, price=305.0, date="d")
        assert gate_fill(buy, Bar("d", 300, 310, 299, 305), None) is not None

    def test_round_lot(self):
        assert assert_round_lot(1) == 1
        for bad in (0, -1, 1.5):
            with pytest.raises(ValueError):
                assert_round_lot(bad)

    def test_hk_close_review(self):
        bar = Bar("d", 300.0, 310.0, 298.0, 305.0)
        fill = hk_close_review_match(
            "00700", lots=1, limit=309.0, stop=290.0, close_bar=bar, slippage=0.05
        )
        assert fill is not None and fill.side == "sell"  # high 310 > 309 严格穿价止盈
        with pytest.raises(ValueError):
            hk_close_review_match(
                "00700",
                lots=1,
                limit=320.0,
                stop=280.0,
                close_bar=Bar("d", 300, 305, 299, 301),
                slippage=0.05,
            )


class TestCorporateActions:
    def _pf_with_buy(self, lots: int, trade_date: str, price: float = 10.0) -> SimPortfolio:
        pf = _portfolio(lots=lots)
        pf.position("600519").avg_cost = price
        pf.position("600519").stop = 8.0
        pf.position("600519").target1 = 12.0
        pf.position("600519").tranches = [
            Tranche(leg="T2_limit", lots=lots, ref_price=price, status="filled", filled_price=price)
        ]
        pf.operations = [
            Operation(
                code="600519",
                side="buy",
                lots=lots,
                price=price,
                ts=f"{trade_date}T02:00:00+00:00",
                trade_date=trade_date,
            )
        ]
        return pf

    def test_split_decimal_recalc(self):
        pf = self._pf_with_buy(lots=10, trade_date="2026-01-05", price=10.0)
        pf = apply_split(
            pf, SplitAction(code="600519", ratio_num=15, ratio_den=10, ex_date="2026-06-01")
        )
        pos = pf.position("600519")
        assert pos.lots == 15  # 10 × 15/10
        assert pos.avg_cost == pytest.approx(10.0 * 10 / 15, rel=1e-3)
        assert pos.stop == pytest.approx(8.0 * 10 / 15, rel=1e-3)
        assert len(pf.operations) == 1  # 不产 Operation 行
        assert pf.corporate_actions[-1].kind == "split"

    def test_dividend_tax_tiers(self):
        assert dividend_tax_rate(10) == 0.20
        assert dividend_tax_rate(30) == 0.20
        assert dividend_tax_rate(31) == 0.10
        assert dividend_tax_rate(365) == 0.10
        assert dividend_tax_rate(366) == 0.0

    def test_dividend_fifo_net_cash(self):
        pf = self._pf_with_buy(lots=10, trade_date="2026-01-05")
        cash_before = pf.cash
        pf = apply_dividend(
            pf,
            DividendAction(
                code="600519", per_share_cash=0.5, ex_date="2026-11-05", pay_date="2026-11-20"
            ),
        )
        gross = 10 * 100 * 0.5  # 500
        # 持股 2026-01-05→11-20 = 319 天 → 10% 税档
        expected_net = gross - gross * 0.10
        assert pf.cash - cash_before == pytest.approx(expected_net, abs=0.01)
        assert pf.corporate_actions[-1].kind == "dividend"

    def test_split_then_dividend_order(self):
        pf = self._pf_with_buy(lots=10, trade_date="2026-01-05")
        pf = apply_corporate_action(pf, SplitAction("600519", 15, 10, "2026-06-01"))
        pf = apply_corporate_action(pf, DividendAction("600519", 0.3, "2026-06-01", "2026-06-10"))
        assert len(pf.corporate_actions) == 2

    def test_fifo_lots(self):
        ops = [
            _buy_op("600519", 2, "2026-01-05"),
            _buy_op("600519", 3, "2026-02-05"),
            Operation(
                code="600519",
                side="sell",
                lots=1,
                price=11.0,
                ts="2026-03-05T02:00:00+00:00",
                trade_date="2026-03-05",
            ),
        ]
        basis = fifo_lots(ops, "600519", as_of="2026-12-31")
        assert [(b.trade_date, b.lots) for b in basis] == [("2026-01-05", 1), ("2026-02-05", 3)]


class TestEngine:
    def _pf(
        self, tranche_lots: int = 1, ref: float = 10.0, stop: float = 9.0, target: float = 11.0
    ) -> SimPortfolio:
        pf = _portfolio(
            tranches=[Tranche(leg="T2_limit", lots=tranche_lots, ref_price=ref, limit_price=ref)]
        )
        pos = pf.position("600519")
        pos.lots = 2
        pos.avg_cost = 10.0
        pos.stop = stop
        pos.target1 = target
        return pf

    def test_t2_touch_produces_request_not_write(self):
        pf = self._pf(ref=10.0)
        bar = Bar("2026-11-08", 10.5, 10.6, 9.95, 10.4, prev_close=10.0)  # low 9.95 < 10.0 严格穿价
        outcome = evaluate_day(pf, DayInput(market_date="2026-11-08", bars={"600519": bar}))
        assert len(outcome.requests) == 1 and outcome.requests[0].tranche_leg == "T2_limit"
        assert pf.position("600519").tranches[0].status == "pending"  # 只推演不写回
        assert pf.cash == 100_000  # 未落账

    def test_suspended_skips_and_keeps_mark(self):
        pf = self._pf()
        pf.position("600519").last_price = 10.4
        halted = Bar("2026-11-08", 0, 0, 0, 0, prev_close=10.4, suspended=True)
        outcome = evaluate_day(pf, DayInput("2026-11-08", {"600519": halted}))
        assert outcome.requests == []
        assert outcome.marks["600519"] == 10.4  # 停牌沿用旧价（不计收益）

    def test_one_word_board_no_request(self):
        pf = self._pf(ref=11.0)
        locked = Bar("2026-11-08", 11.0, 11.0, 11.0, 11.0, prev_close=10.0)
        outcome = evaluate_day(pf, DayInput("2026-11-08", {"600519": locked}))
        assert outcome.requests == []

    def test_hk_requires_final(self):
        pf = self._pf()
        pos = pf.position("600519")
        pos.market = "HK"
        pos.board_lot = 100
        bar = Bar("2026-11-08", 9.0, 10.5, 8.9, 9.2)  # 触止损区间
        mid = evaluate_day(pf, DayInput("2026-11-08", {"600519": bar}, is_final=False))
        assert mid.requests == []  # 盘中零撮合（D8 定谳前收盘复核）
        final = evaluate_day(pf, DayInput("2026-11-08", {"600519": bar}, is_final=True))
        # 止损 fill 的 leg 由 matcher 注入（stop/limit）；仓位级卖出请求在场即证 HK 收盘复核
        assert any(r.side == "sell" for r in final.requests)

    def test_gtc_expiry_collected(self):
        pf = self._pf()
        pf.position("600519").tranches[0].expires_at = "2026-11-07"
        bar = Bar("2026-11-08", 10.5, 10.6, 10.2, 10.4, prev_close=10.4)
        outcome = evaluate_day(pf, DayInput("2026-11-08", {"600519": bar}))
        assert ("600519", "T2_limit") in outcome.expired_legs

    def test_mark_equity_appends_and_overrides_same_day(self):
        pf = self._pf()
        pf = mark_equity(pf, "2026-11-08", {"600519": 10.5})
        assert pf.equity_history[-1].date == "2026-11-08"
        assert pf.position("600519").last_price == 10.5
        pf = mark_equity(pf, "2026-11-08", {"600519": 10.8})  # 同日重跑覆盖
        assert len([p for p in pf.equity_history if p.date == "2026-11-08"]) == 1
        assert pf.equity_history[-1].nav == pytest.approx((100_000 + 2 * 100 * 10.8) / 100_000)

    def test_request_from_t2_event(self):
        facts = {
            "leg": "T2_limit",
            "ref_price": 10.0,
            "lots": 1,
            "price": 9.98,
            "date": "2026-11-08",
        }
        req = request_from_t2_event("600519", facts, "2026-11-08T02:00:00Z", request_id="pf-x")
        assert req is not None and req.tranche_leg == "T2_limit" and req.side == "buy"
        assert request_from_t2_event("600519", {"leg": "T2_limit"}, "t", request_id="pf-y") is None


class TestPendingQueue:
    def _req(self, rid: str, side: str = "buy", lots: int = 1):
        fill = Fill(
            code="600519",
            side=side,
            lots=lots,
            price=9.6,
            date="2026-11-08",
            tranche_leg="T2_limit",
        )
        return request_from_fill(fill, request_id=rid, ref_price=9.6, ts="2026-11-08T02:00:00Z")

    def test_roundtrip_and_frozen(self, tmp_path: Path):
        path = tmp_path / "sim-pending-fills.json"
        q = PendingQueue()
        q.enqueue(self._req("a", side="buy"))
        q.enqueue(self._req("b", side="sell", lots=2))
        psave(q, path)
        loaded = pload(path)
        assert [r.request_id for r in loaded.requests] == ["a", "b"]
        assert loaded.frozen_sell_lots("600519") == 2
        loaded.confirm("a")
        assert loaded.confirmed("a").state == "confirmed"
        loaded.remove("a")
        assert loaded.find("a") is None
        assert pload(path).frozen_sell_lots("600519") == 2  # 盘上仍是旧态（save 才落）

    def test_enqueue_idempotent(self):
        q = PendingQueue().enqueue(self._req("a"))
        q.enqueue(self._req("a"))
        assert len(q.requests) == 1

    def test_estimated_markers(self):
        assert RULES_ESTIMATED and CA_ESTIMATED
