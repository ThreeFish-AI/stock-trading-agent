"""静态滑点表（D7：estimated 起步，自家成交回报样本校准后固化；只许收严）。

A 股默认 10bp（散户小单冲击重要性仅 3.63%，E-18；无现成锚 E-73/E-75）；
港股按最小价位分档 1 tick（0.25–10 港元档 0.005 港元，E-55；仙股 1 tick ≈ 167bp）。
量相依冲击（Almgren 3/5 幂律、A 股指数 0.7，E-16/E-17）留大额/低流动性升级位（M2 接口）。
"""

from __future__ import annotations

ESTIMATED = True  # 同 fees.py：estimated 标记在场测试锚点

A_SHARE_DEFAULT_BP = 10.0  # A 股默认滑点 10bp（D7 推荐默认）

# 港股最小价位分档（价格上界 → 最小变动价位，E-55：0.25–10 港元档 0.005，仙股 1 tick≈167bp）
HK_TICK_TABLE: tuple[tuple[float, float], ...] = (
    (0.25, 0.001),
    (10.00, 0.005),
    (20.00, 0.050),
    (100.00, 0.100),
    (200.00, 0.200),
    (500.00, 0.500),
    (1000.00, 1.000),
    (2000.00, 2.000),
    (5000.00, 5.000),
)


def a_share_slippage(price: float, *, bp: float = A_SHARE_DEFAULT_BP) -> float:
    """A 股滑点（元）：价 × bp/10_000。"""
    return round(price * bp / 10_000, 4)


def hk_tick(price: float) -> float:
    """港股最小变动价位（按档查表；高于表顶按表顶档）。"""
    for band_top, tick in HK_TICK_TABLE:
        if price <= band_top:
            return tick
    return HK_TICK_TABLE[-1][1]


def hk_slippage(price: float) -> float:
    """港股滑点（港元）＝1 tick（保守取不利方向一个最小价位）。"""
    return hk_tick(price)
