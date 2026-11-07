"""静态费用栈（分段常量 + 纯函数）。

口径锚：方案 §12 费用栈（E-10~E-15）；数值全部 **estimated**（D7 精神：以自家成交回报样本
校准后固化，校准前只许收严不许放宽——tests 机械检查 ESTIMATED 标记在场）。
不沿用任何引擎默认值（E-73/E-74 未核验、仅作线索）。
"""

from __future__ import annotations

from dataclasses import dataclass

ESTIMATED = True  # 全栈 estimated 标记（测试断言在场；放宽须人工评审并登记 CHANGELOG）


@dataclass(frozen=True)
class MarketFees:
    market: str  # A | HK
    stamp_duty_bp: float  # 印花税（bp；A 股卖出单边 5；HK 双边 10）
    transfer_fee_bp: float  # 过户费（bp；A 股双边 0.1；HK 0）
    trade_fee_bp: float  # 交易费（bp；HK 0.00565%/边 → 0.565bp/边）
    sfc_levy_bp: float  # SFC（bp；HK 0.0027% → 0.27bp）
    afrc_levy_bp: float  # AFRC（bp；HK 0.00015% → 0.015bp）
    commission_min: float  # 佣金最低（元/港元；A 股 5 元，费率留用户配置位；HK 0）


A_SHARE_FEES = MarketFees(
    market="A",
    stamp_duty_bp=5.0,
    transfer_fee_bp=0.1,
    trade_fee_bp=0.0,
    sfc_levy_bp=0.0,
    afrc_levy_bp=0.0,
    commission_min=5.0,
)
HK_FEES = MarketFees(
    market="HK",
    stamp_duty_bp=10.0,
    transfer_fee_bp=0.0,
    trade_fee_bp=0.565,
    sfc_levy_bp=0.27,
    afrc_levy_bp=0.015,
    commission_min=0.0,
)


def _ceil_to(value: float, unit: float) -> float:
    import math

    return math.ceil(value / unit) * unit


def calc_fees(
    market: str, side: str, notional: float, *, ccass_min: float = 2.0, ccass_max: float = 100.0
) -> float:
    """计算一手成交的全部费用（元/港元）。

    A 股：卖出印花 5bp 单边 + 过户费 0.1bp 双边 + 佣金最低 5 元（费率位留用户配置）。
    HK：印花 10bp 双边向上取整 1 港元 + 交易费/SFC/AFRC 按边 + CCASS 0.002%（min 2 max 100）。
    """
    if market == "A":
        fees = A_SHARE_FEES.transfer_fee_bp / 10_000 * notional  # 过户费双边
        if side == "sell":
            fees += A_SHARE_FEES.stamp_duty_bp / 10_000 * notional
        fees += A_SHARE_FEES.commission_min if notional > 0 else 0.0
        return round(fees, 2)
    if market == "HK":
        fees = _ceil_to(HK_FEES.stamp_duty_bp / 10_000 * notional, 1.0)  # 印花向上取整 1 港元
        fees += HK_FEES.trade_fee_bp / 10_000 * notional
        fees += HK_FEES.sfc_levy_bp / 10_000 * notional
        fees += HK_FEES.afrc_levy_bp / 10_000 * notional
        ccass = HK_FEES_CCASS_BP / 10_000 * notional
        fees += min(max(ccass, ccass_min), ccass_max)
        return round(fees, 2)
    raise ValueError(f"未知市场：{market}（仅 A | HK）")


HK_FEES_CCASS_BP = 0.2  # CCASS 0.002% = 0.2bp（min 2 max 100 港元）
