"""严格穿价撮合纯函数（零 LLM、零网络）。

口径锚（vibe-stock，不复制文字）：references/07「成交判据与提示判据分两把尺」——
成交判据取严格穿价（买入 low < limit），提示口径（low ≤ entry_ref）只推演不写回；
references/06 Stage D（T1 跳空 >+2% 转限价 = 信号日收盘 ×1.02、5 日 GTC、基准不滚动）。
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_TICK = 0.01  # 默认 tick 穿越旋钮 ≥1 tick（E-04）；A 股最小变动 0.01 元


@dataclass(frozen=True)
class Bar:
    """一根 OHLC 日线（撮合的最小输入单元；时戳新鲜度门禁见 07/方案 §12 港股门禁）。"""

    date: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Fill:
    code: str
    side: str  # buy | sell
    lots: int
    price: float
    date: str
    tranche_leg: str | None = None


_EPS = 1e-9  # 浮点容差：恰 1 tick 的十进制差在二进制下可能略小于 tick


def _tick_ok(bar_low: float, limit: float, tick: float) -> bool:
    """tick 穿越旋钮：穿价幅度须 ≥ tick（默认 0.01；浮点容差内视为达标）。"""
    return (limit - bar_low) >= tick - _EPS


def match_limit_buy(
    code: str, lots: int, limit: float, bar: Bar, *, tick: float = MIN_TICK, leg: str | None = None
) -> Fill | None:
    """限价买入：严格穿价 low < limit 且价 = min(open, limit)；边界 low == limit 不成交。"""
    if not bar.low < limit:
        return None
    if not _tick_ok(bar.low, limit, tick):
        return None
    price = min(bar.open, limit)
    return Fill(code=code, side="buy", lots=lots, price=price, date=bar.date, tranche_leg=leg)


def match_limit_sell(
    code: str, lots: int, limit: float, bar: Bar, *, tick: float = MIN_TICK, leg: str | None = None
) -> Fill | None:
    """限价卖出（镜像）：严格穿价 high > limit、价 = max(open, limit)。"""
    if not bar.high > limit:
        return None
    if not (bar.high - limit) >= tick - _EPS:
        return None
    price = max(bar.open, limit)
    return Fill(code=code, side="sell", lots=lots, price=price, date=bar.date, tranche_leg=leg)


def match_stop(
    code: str, lots: int, stop: float, bar: Bar, *, slippage: float = 0.0, leg: str | None = None
) -> Fill | None:
    """止损卖出：价 = min(open, stop) − 滑点（开盘跳空按更差价成交；悲观序见下）。"""
    if bar.low >= stop and bar.open >= stop:
        return None
    # 开盘即穿按开盘价（更差价），盘中击穿按止损价
    price = bar.open - slippage if bar.open <= stop else stop - slippage
    return Fill(code=code, side="sell", lots=lots, price=price, date=bar.date, tranche_leg=leg)


def match_bar_pessimistic(
    code: str,
    lots: int,
    limit: float,
    stop: float,
    bar: Bar,
    *,
    slippage: float = 0.0,
    tick: float = MIN_TICK,
) -> Fill:
    """同 bar 止盈（限价卖）与止损并发：止损先发生的悲观序（触价口径摆动 ±240bps，E-05）。"""
    stop_fill = match_stop(code, lots, stop, bar, slippage=slippage, leg="stop")
    if stop_fill is not None:
        return stop_fill
    limit_fill = match_limit_sell(code, lots, limit, bar, tick=tick, leg="limit")
    if limit_fill is None:
        raise ValueError(f"bar 未触发任何单：limit={limit} stop={stop} bar={bar}")
    return limit_fill


def t1_gap_fallback_limit(signal_day_close: float) -> float:
    """T1 跳空回落口径（06 Stage D）：次日开盘跳空 > +2% 时 T1 开盘批转限价 = 信号日收盘 ×1.02。"""
    return round(signal_day_close * 1.02, 2)


def t1_gap_needs_fallback(signal_day_close: float, next_open: float) -> bool:
    """跳空判定：next_open > signal_day_close × 1.02（>+2% 转限价；等于不转，06 口径 > 严格）。"""
    return next_open > round(signal_day_close * 1.02, 2)
