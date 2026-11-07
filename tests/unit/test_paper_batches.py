"""批状态机、仓位推导与落账（口径：vibe-stock references/07 sim 组合 SSOT 节）。"""

from __future__ import annotations

import pytest

from stock_trading_agent.paper.batches import (
    BatchTransitionError,
    derive_position_status,
    expire_tranche,
    fill_tranche,
    sync_position,
)
from stock_trading_agent.paper.confirm import ConfirmError, confirm, reject, request_from_fill
from stock_trading_agent.paper.fills import Fill
from stock_trading_agent.paper.ledger import LedgerError, apply_fill, close_position, open_position
from stock_trading_agent.paper.model import SimPortfolio, Tranche


def tranche(leg: str = "T2_limit", status: str = "pending", ref: float = 10.0) -> Tranche:
    return Tranche(leg=leg, lots=1, status=status, ref_price=ref)


class TestBatchMachine:
    def test_fill_writes_price_and_time(self):
        t = fill_tranche(tranche(), filled_price=9.6, filled_at="2026-11-07T01:23:45Z")
        assert (t.status, t.filled_price, t.filled_at) == ("filled", 9.6, "2026-11-07T01:23:45Z")

    def test_fill_rejects_non_pending(self):
        with pytest.raises(BatchTransitionError):
            fill_tranche(tranche(status="filled"), 9.6, "ts")
        with pytest.raises(BatchTransitionError):
            fill_tranche(tranche(status="expired"), 9.6, "ts")

    def test_expire_only_from_pending(self):
        assert expire_tranche(tranche()).status == "expired"
        with pytest.raises(BatchTransitionError):
            expire_tranche(tranche(status="filled"))
        with pytest.raises(BatchTransitionError):
            expire_tranche(tranche(status="expired"))  # 两终态互斥不可逆


class TestDerivePosition:
    def test_spectrum(self):
        t = tranche()
        assert derive_position_status([t]) == "pending_open"
        assert derive_position_status([t.model_copy(update={"status": "filled"})]) == "holding"
        # 任一批成交即 holding（其余批 pending 不影响）
        two = [t.model_copy(update={"status": "filled"}), tranche("T3_trigger")]
        assert derive_position_status(two) == "holding"
        # 所有存在的批均 expired 且无成交 → 建仓失败 closed
        assert derive_position_status([t.model_copy(update={"status": "expired"})]) == "closed"
        assert derive_position_status([]) == "pending_open"

    def test_close_requested(self):
        filled = tranche(status="filled")
        assert derive_position_status([filled], closed_requested=True) == "closed"

    def test_sync_zeroes_lots_on_closed(self):
        from stock_trading_agent.paper.model import Position

        pos = Position(code="600519", market="A", lots=2, tranches=[tranche(status="filled")])
        synced = sync_position(pos, closed_requested=True)
        assert synced.status == "closed" and synced.lots == 0


class TestConfirmFlow:
    def fill_req(self, state: str = "pending"):
        fill = Fill(
            code="600519", side="buy", lots=1, price=9.6, date="2026-11-07", tranche_leg="T2_limit"
        )
        req = request_from_fill(fill, request_id="r1", ref_price=9.6, ts="2026-11-07T01:00:00Z")
        from dataclasses import replace

        return replace(req, state=state)

    def test_confirm_and_reject(self):
        assert confirm(self.fill_req()).state == "confirmed"
        assert reject(self.fill_req()).state == "rejected"

    def test_double_transition_rejected(self):
        with pytest.raises(ConfirmError):
            confirm(self.fill_req("confirmed"))
        with pytest.raises(ConfirmError):
            reject(self.fill_req("rejected"))


class TestLedgerApply:
    def portfolio(self) -> SimPortfolio:
        pf = SimPortfolio(budget=100_000, cash=100_000)
        return open_position(pf, "600519", "A", [tranche("T2_limit", ref=10.0)], entry_ref=10.0)

    def confirmed_req(self):
        return TestConfirmFlow().fill_req("confirmed")

    def test_unconfirmed_never_lands(self):
        from dataclasses import replace

        with pytest.raises(LedgerError):
            apply_fill(self.portfolio(), replace(self.confirmed_req(), state="pending"))
        with pytest.raises(LedgerError):
            apply_fill(self.portfolio(), replace(self.confirmed_req(), state="rejected"))

    def test_apply_fill_updates_book(self):
        pf = apply_fill(self.portfolio(), self.confirmed_req())
        pos = pf.position("600519")
        assert pos.status == "holding" and pos.lots == 1 and pos.tranches[0].status == "filled"
        assert pos.avg_cost == pytest.approx(9.6)  # 成交价 = ref_price（触价确认价）
        assert len(pf.operations) == 1 and pf.operations[0].tranche_ref == "T2_limit"
        notional = 1 * 100 * 9.6
        from stock_trading_agent.paper.fees import calc_fees

        expect_cash = 100_000 - notional - calc_fees("A", "buy", notional)
        assert pf.cash == pytest.approx(expect_cash, abs=0.01)

    def test_cash_insufficient_rejected(self):
        from stock_trading_agent.paper.model import SimPortfolio as SP

        pf = SP(budget=100, cash=100)
        pf = open_position(pf, "600519", "A", [tranche("T2_limit", ref=10.0)])
        with pytest.raises(LedgerError, match="现金不足"):
            apply_fill(pf, self.confirmed_req())

    def test_close_expires_pending_batches(self):
        pf = self.portfolio()
        pf = close_position(pf, "600519")
        pos = pf.position("600519")
        assert pos.status == "closed"
        assert all(t.status == "expired" for t in pos.tranches)

    def test_apply_requires_matching_pending_tranche(self):
        pf = self.portfolio()
        pf = apply_fill(pf, self.confirmed_req())
        with pytest.raises(LedgerError, match="待成交批"):
            apply_fill(pf, self.confirmed_req())
