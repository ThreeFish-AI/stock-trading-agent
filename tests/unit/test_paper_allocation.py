"""allocation 导入纯函数：06 Stage D 产物解析/校验/三批建仓（0.0.31）。"""

from __future__ import annotations

import pytest

from stock_trading_agent.paper.allocation import (
    AllocationError,
    import_allocation,
    new_portfolio_from_allocation,
    parse_allocation,
)
from stock_trading_agent.paper.model import SimPortfolio

ALLOC = {
    "budget": 100_000.0,
    "rows": [
        {
            "code": "601919",
            "price": 16.67,
            "stop": 15.84,
            "entry_ref": 16.17,
            "target1": 16.87,
            "ma20": 16.279,
            "lots": 6,
            "tranches": {"T1_open": 2, "T2_limit_entryref": 2, "T3_trigger": 2},
        }
    ],
}


class TestParseAllocation:
    def test_row_to_tranche_inputs(self):
        rows = parse_allocation(ALLOC)
        assert len(rows) == 1
        row = rows[0]
        assert row.market == "A" and row.code == "601919"
        assert (row.t1_lots, row.t2_lots, row.t3_lots) == (2, 2, 2)
        assert row.ma20 == 16.279 and row.entry_ref == 16.17

    def test_hk_market_derived_by_digit_count(self):
        data = {
            "rows": [
                {
                    "code": "00700",
                    "price": 420.0,
                    "stop": 400.0,
                    "entry_ref": 415.0,
                    "target1": 460.0,
                    "ma20": 430.0,
                    "lots": 2,
                    "tranches": {"T1_open": 1, "T2_limit_entryref": 1, "T3_trigger": 0},
                }
            ]
        }
        assert parse_allocation(data)[0].market == "HK"

    def test_unknown_code_shape_rejected(self):
        with pytest.raises(AllocationError, match="无法判定市场"):
            parse_allocation({"rows": [dict(ALLOC["rows"][0], code="6005199")]})

    def test_lots_must_conserve(self):
        with pytest.raises(AllocationError, match="手数不守恒"):
            parse_allocation(
                {
                    "rows": [
                        dict(
                            ALLOC["rows"][0],
                            tranches={"T1_open": 3, "T2_limit_entryref": 2, "T3_trigger": 2},
                        )
                    ]
                }
            )

    def test_price_order_monotonic(self):
        with pytest.raises(AllocationError, match="三价不单调"):
            parse_allocation({"rows": [dict(ALLOC["rows"][0], stop=16.5)]})
        with pytest.raises(AllocationError, match="三价不单调"):
            parse_allocation({"rows": [dict(ALLOC["rows"][0], target1=16.0)]})

    def test_empty_rows_rejected(self):
        with pytest.raises(AllocationError, match="无有效行"):
            parse_allocation({"rows": []})


class TestImportAllocation:
    def test_new_portfolio_budget_cash_init(self):
        pf = new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10")
        assert pf.budget == 100_000.0 and pf.cash == 100_000.0  # sim 全额可用（cash_left 不扣）
        pos = pf.position("601919")
        assert pos.market == "A" and pos.opened_date == "2026-11-10"
        assert pos.status == "pending_open"
        t1, t2, t3 = pos.tranches
        assert (t1.leg, t1.ref_price, t1.limit_price) == ("T1_open", 16.67, None)
        assert (t2.leg, t2.ref_price, t2.limit_price) == ("T2_limit", 16.17, 16.17)
        assert t2.expires_at == "2026-11-24"  # T2 10 日 GTC → +14 日历日（ESTIMATED）
        assert (t3.leg, t3.ref_price) == ("T3_trigger", 16.279)  # T3 回踩锚=MA20
        assert all(t.status == "pending" and t.judged_date is None for t in pos.tranches)
        assert pos.t3_ma20 == 16.279  # T3 复合判定锚（judge_t3）

    def test_zero_lot_tranche_filtered(self):
        data = {
            "budget": 100_000.0,
            "rows": [
                dict(
                    ALLOC["rows"][0],
                    tranches={"T1_open": 6, "T2_limit_entryref": 0, "T3_trigger": 0},
                )
            ],
        }
        pf = new_portfolio_from_allocation(data, opened_date="2026-11-10")
        assert [t.leg for t in pf.position("601919").tranches] == ["T1_open"]

    def test_append_into_existing_and_dup_rejected_wholesale(self):
        pf = new_portfolio_from_allocation(ALLOC, opened_date="2026-11-10")
        other = {"rows": [dict(ALLOC["rows"][0], code="600519")]}
        grown = import_allocation(pf, parse_allocation(other), opened_date="2026-11-12")
        assert grown.position("600519") is not None
        assert grown.position("601919").opened_date == "2026-11-10"  # 原仓导入日不动
        assert grown.position("600519").opened_date == "2026-11-12"
        # dup：整批拒绝（先全量预检，防半批导入）
        with pytest.raises(Exception, match="已在账本"):
            import_allocation(grown, parse_allocation(ALLOC), opened_date="2026-11-13")

    def test_import_requires_portfolio(self):
        with pytest.raises(Exception, match="账本未初始化"):
            import_allocation(None, parse_allocation(ALLOC), opened_date="2026-11-10")

    def test_legacy_portfolio_without_opened_date_is_untouched(self):
        pf = SimPortfolio(budget=1.0, cash=1.0)
        assert pf.positions == []
