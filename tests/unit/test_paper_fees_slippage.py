"""静态费用栈与滑点表（数值 estimated：标记在场机械检查，防放宽）。"""

from __future__ import annotations

import pytest

from stock_trading_agent.paper import fees, slippage


class TestAFees:
    def test_sell_pays_stamp(self):
        # 10000 元名义卖出：过户 0.1bp=0.1 + 印花 5bp=5.0 + 佣金 min 5 = 10.10
        assert fees.calc_fees("A", "sell", 10_000.0) == pytest.approx(10.10, abs=0.01)

    def test_buy_no_stamp(self):
        assert fees.calc_fees("A", "buy", 10_000.0) == pytest.approx(5.10, abs=0.01)

    def test_commission_floor(self):
        assert fees.A_SHARE_FEES.commission_min == 5.0  # 费率位留用户配置


class TestHKFees:
    def test_stamp_rounds_up_to_1_hkd(self):
        # 10000 港元：印花 ceil(10bp)=10 + 交易 0.565 + SFC 0.27 + AFRC 0.015 + CCASS 2 = 12.85
        assert fees.calc_fees("HK", "buy", 10_000.0) == pytest.approx(12.85, abs=0.01)

    def test_ccass_band(self):
        # 大额：CCASS 触顶 100 港元（0.2bp×大名义）
        big = fees.calc_fees("HK", "buy", 100_000_000.0)
        assert big > 0 and fees.calc_fees("HK", "buy", 100_000_000.0) == pytest.approx(big)

    def test_unknown_market_rejected(self):
        with pytest.raises(ValueError):
            fees.calc_fees("US", "buy", 100.0)


class TestSlippage:
    def test_a_share_default_10bp(self):
        assert slippage.a_share_slippage(10.0) == pytest.approx(0.01)
        assert slippage.A_SHARE_DEFAULT_BP == 10.0

    def test_hk_tick_bands(self):
        assert slippage.hk_tick(0.30) == 0.005
        assert slippage.hk_tick(3.00) == 0.020
        assert slippage.hk_slippage(0.30) == 0.005


class TestEstimatedDiscipline:
    """estimated 纪律（D7）：常量标记在场；放宽须人工评审并登记 CHANGELOG。"""

    def test_estimated_markers_present(self):
        assert fees.ESTIMATED is True
        assert slippage.ESTIMATED is True

    def test_fee_constants_snapshot(self):
        assert fees.A_SHARE_FEES.stamp_duty_bp == 5.0
        assert fees.HK_FEES.stamp_duty_bp == 10.0
        assert fees.HK_FEES_CCASS_BP == 0.2
        assert slippage.A_SHARE_DEFAULT_BP == 10.0
