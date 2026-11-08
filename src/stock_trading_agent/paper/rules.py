"""A 股市场规则闸：涨跌停三态、停牌、整手、HK 收盘复核（E-09/E-26/E-41/E-25/E-76）。

保守裁定（13-paper §③）：触及未封死=价格 clamp 进板内成交（板价成交，不模拟按比例排队
份额——登记 estimated 升级位）；一字板不利向不成交；停牌全拒。涨停价按最小变动单位取整
（E-09：非写 return>=0.1）。HK 无涨跌停三态（VCM 市调机制不模拟）；D8 定谳前 HK 撮合
唯一入口=hk_close_review_match（只吃日终收盘 bar，盘中零撮合请求）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stock_trading_agent.paper.fills import (
    MIN_TICK,
    Bar,
    Fill,
    match_bar_pessimistic,
)

ESTIMATED = True  # 全模块口径 estimated：幅度常量与排队份额建模待自家样本校准后固化
A_LIMIT_PCT = 0.10  # 主板 10%（estimated）；创业板/科创板 20%、ST 5% 留常量位不启用


@dataclass(frozen=True)
class LimitBand:
    upper: float  # 涨停价（按最小变动单位向内取整——保守内收）
    lower: float  # 跌停价（同上）


def limit_band(prev_close: float, *, pct: float = A_LIMIT_PCT, tick: float = MIN_TICK) -> LimitBand:
    """涨跌停板价：prev_close×(1±pct) 后按最小变动单位向内取整（E-09 取整纪律）。"""
    raw_up = prev_close * (1 + pct)
    raw_down = prev_close * (1 - pct)
    upper = math.floor(raw_up / tick) * tick
    lower = math.ceil(raw_down / tick) * tick
    return LimitBand(upper=round(upper, 2), lower=round(lower, 2))


def band_state(bar: Bar, band: LimitBand) -> str:
    """三态判定：normal | touched_up/touched_down（触及打开）| locked_up/locked_down（一字）。"""
    eps = 1e-9
    one_word_up = (
        abs(bar.open - band.upper) < eps
        and abs(bar.high - band.upper) < eps
        and abs(bar.low - band.upper) < eps
    )
    one_word_down = (
        abs(bar.open - band.lower) < eps
        and abs(bar.high - band.lower) < eps
        and abs(bar.low - band.lower) < eps
    )
    if one_word_up:
        return "locked_up"
    if one_word_down:
        return "locked_down"
    if bar.high >= band.upper - eps:
        return "touched_up"
    if bar.low <= band.lower + eps:
        return "touched_down"
    return "normal"


def gate_fill(fill: Fill | None, bar: Bar, band: LimitBand | None) -> Fill | None:
    """市场规则闸（撮合后置过滤/夹价）：停牌全拒；一字板不利向不成交；触及 clamp 进板。

    - bar.suspended → None（E-26 停牌禁买卖）
    - 一字涨停（locked_up）买入不成交 / 一字跌停（locked_down）卖出不成交（E-09）
    - 触及打开（touched_*）：成交价 clamp 进板内（买 ≤ upper、卖 ≥ lower）——保守裁定
    """
    if fill is None or bar.suspended:
        return None
    if band is None:
        return fill
    state = band_state(bar, band)
    eps = 1e-9
    if fill.side == "buy" and state == "locked_up":
        return None
    if fill.side == "sell" and state == "locked_down":
        return None
    if fill.side == "buy" and fill.price > band.upper + eps:
        fill = _replace_price(fill, min(fill.price, band.upper))
    if fill.side == "sell" and fill.price < band.lower - eps:
        fill = _replace_price(fill, max(fill.price, band.lower))
    return fill


def _replace_price(fill: Fill, price: float) -> Fill:
    from dataclasses import replace

    return replace(fill, price=round(price, 4))


def assert_round_lot(lots: int) -> int:
    """整手闸（E-41）：非正整数手数拒（买卖均须整手；零股卖出不模拟）。"""
    if not isinstance(lots, int) or lots < 1:
        raise ValueError(f"整手闸违例：lots={lots!r}（须 ≥1 的整数手）")
    return lots


def hk_close_review_match(
    code: str,
    lots: int,
    limit: float,
    stop: float,
    close_bar: Bar,
    *,
    slippage: float,
) -> Fill:
    """HK 撮合唯一入口（D8 定谳前收盘复核成交，E-25/E-76 保守处置）。

    只吃当日最终收盘 bar（调用方保证日终；盘中零撮合请求）；band=None（HK 无三态）；
    止盈/止损按悲观序复用 A 股同一 matcher（判据同式）。
    """
    return match_bar_pessimistic(
        code, lots=lots, limit=limit, stop=stop, bar=close_bar, slippage=slippage
    )
