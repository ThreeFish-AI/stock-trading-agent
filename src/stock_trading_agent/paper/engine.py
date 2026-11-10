"""paper 日终引擎：evaluate_day（撮合→待确认请求）+ mark_equity（权益曲线）+ t2 事件适配。

纯确定性模块（NR-2：零 LLM、零网络、永不接真实下单）；触价只产待确认请求（07：只推演
不写回），用户逐笔确认后经 ledger.apply_fill 落账（唯一入口）。停牌跳过撮合且 mark 沿用
旧值（E-26「不计收益」实现面）；HK 仅日终收盘 bar 走收盘复核（D8 定谳前，E-25/E-76）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from stock_trading_agent.paper.confirm import PendingFillRequest
from stock_trading_agent.paper.fills import Bar, Fill, match_limit_buy
from stock_trading_agent.paper.model import SimPortfolio
from stock_trading_agent.paper.rules import ESTIMATED, gate_fill, limit_band
from stock_trading_agent.paper.slippage import a_share_slippage, hk_slippage

__all__ = [
    "ESTIMATED",
    "DayInput",
    "EngineOutcome",
    "T1Judgement",
    "T3Judgement",
    "evaluate_day",
    "judge_t1_open",
    "judge_t3",
    "mark_equity",
    "request_from_t2_event",
]


@dataclass(frozen=True)
class DayInput:
    market_date: str
    bars: dict[str, Bar]  # code → 当日终 bar（含 prev_close/suspended）
    is_final: bool = True  # 日终态（False 时 HK 零撮合）


@dataclass(frozen=True)
class EngineOutcome:
    requests: list[PendingFillRequest] = field(default_factory=list)
    expired_legs: list[tuple[str, str]] = field(default_factory=list)  # (code, leg)
    marks: dict[str, float] = field(default_factory=dict)  # code → last_price 更新集


@dataclass(frozen=True)
class T1Judgement:
    """T1 开盘批一次性判定结果（judge_t1_open；调用方按 outcome 落请求/转限价并盖戳）。"""

    request: PendingFillRequest | None = None  # 开盘价成交（无跳空）→ 待确认请求
    convert_limit: float | None = None  # 跳空 >+2%（或闸拒）→ 转限价 = 信号日收盘 ×1.02
    convert_expires: str | None = None  # GTC 窗口终点（+7 日历日 ≈ 5 交易日，ESTIMATED）


def judge_t1_open(
    position,
    tranche,
    bar: Bar,
    ts: str = "",
) -> T1Judgement:
    """T1 开盘批「次日交易日」一次性判定（06 Stage D；evaluate_day 不驱动 T1，本函数独占）。

    口径：开盘无跳空（open ≤ 收盘×1.02）→ 开盘价成交产待确认请求（fill_price=open）；
    跳空 >+2% → 转限价 t1_gap_fallback_limit（×1.02）、5 日 GTC（+7 日历日近似，ESTIMATED）；
    一字板不利向闸拒（理论不可达：无跳空即非一字涨停；防御位）同转限价保守处置。
    日历无关：调用方（EOD 驱动）保证「导入后首个 ticks 在场日」即次日交易日。
    """
    from stock_trading_agent.paper.confirm import request_from_fill
    from stock_trading_agent.paper.fills import (
        Fill,
        t1_gap_fallback_limit,
        t1_gap_needs_fallback,
    )

    signal_day_close = tranche.ref_price
    expires = (dt.date.fromisoformat(bar.date) + dt.timedelta(days=7)).isoformat()
    if t1_gap_needs_fallback(signal_day_close, bar.open):
        return T1Judgement(
            convert_limit=t1_gap_fallback_limit(signal_day_close), convert_expires=expires
        )
    fill = Fill(
        code=position.code,
        side="buy",
        lots=tranche.lots,
        price=bar.open,
        date=bar.date,
        tranche_leg="T1_open",
    )
    band = limit_band(bar.prev_close) if position.market == "A" and bar.prev_close else None
    fill = gate_fill(fill, bar, band)
    if fill is None:  # 防御位：开盘可成交日被闸（理论不可达）→ 保守转限价
        return T1Judgement(
            convert_limit=t1_gap_fallback_limit(signal_day_close), convert_expires=expires
        )
    ts = ts or dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")
    req = request_from_fill(
        fill, _request_id(position.code, "T1_open", fill.date), signal_day_close, ts
    )
    return T1Judgement(request=req)


@dataclass(frozen=True)
class T3Judgement:
    """T3 触发批判定结果（judge_t3；None 字段=本日无动作，批保持 pending 继续逐日判）。"""

    request: PendingFillRequest | None = None  # 触发/补足成交 → 待确认请求
    reason: str = ""  # above_target1 | pullback_ma20 | window_fallback


def judge_t3(
    position,
    tranche,
    bar: Bar,
    *,
    window_end: str,
    ts: str = "",
) -> T3Judgement:
    """T3 触发批逐日判定（06 Stage D 复合口径；evaluate_day 不驱动 T3，本函数独占）。

    三臂（先到先得，触发即盖戳不再逐日）：①站上目标1（close > target1）→ 收盘价成交；
    ②回踩 MA20±1%（low ≤ ma20×1.01 ∧ close ≥ ma20×0.99）→ 成交价 = ma20 夹入
    当日 [low, high]（band 触及近似，ESTIMATED——日 K 内触达次序不可知）；
    ③窗口终点未触发 → 按收盘补足（「20 日内未触发按收盘补足」；窗口锚 opened_date，
    由调用方算 window_end=+28 日历日 ≈ 20 交易日，ESTIMATED）。
    t3_ma20/target1 缺席（旧仓位/缺锚）→ 恒不触发（宁缺毋猜，待补锚）。
    """
    if position.t3_ma20 is None or position.target1 is None:
        return T3Judgement()
    ma20, target1 = position.t3_ma20, position.target1
    if bar.close > target1:
        fill = _t3_fill(position, tranche, bar, bar.close, ts)
        return T3Judgement(request=fill, reason="above_target1")
    if bar.low <= ma20 * 1.01 and bar.close >= ma20 * 0.99:
        price = min(max(ma20, bar.low), bar.high)
        fill = _t3_fill(position, tranche, bar, price, ts)
        return T3Judgement(request=fill, reason="pullback_ma20")
    if window_end and bar.date >= window_end:
        fill = _t3_fill(position, tranche, bar, bar.close, ts)
        return T3Judgement(request=fill, reason="window_fallback")
    return T3Judgement()


def _t3_fill(position, tranche, bar: Bar, price: float, ts: str) -> PendingFillRequest:
    from stock_trading_agent.paper.confirm import request_from_fill
    from stock_trading_agent.paper.fills import Fill

    ts = ts or dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")
    fill = Fill(
        code=position.code,
        side="buy",
        lots=tranche.lots,
        price=price,
        date=bar.date,
        tranche_leg="T3_trigger",
    )
    return request_from_fill(
        fill, _request_id(position.code, "T3_trigger", fill.date), tranche.ref_price, ts
    )


def _request_id(code: str, leg: str, date: str) -> str:
    return f"pf-{code}-{leg}-{date}"


def evaluate_day(
    portfolio: SimPortfolio,
    day: DayInput,
    request_ts: str = "",
) -> EngineOutcome:
    """日终驱动：逐持仓撮合（触价只产待确认请求）+ GTC 到期 + 权益标记价收集。"""
    outcome = EngineOutcome(requests=[], expired_legs=[], marks={})
    ts = request_ts or dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")
    for position in portfolio.positions:
        if position.status == "closed":
            continue
        bar = day.bars.get(position.code)
        if bar is None:
            continue
        if bar.suspended:
            outcome.marks[position.code] = position.last_price or bar.close  # 停牌沿用旧价
            _expire_due(position, day.market_date, outcome)
            continue
        band = limit_band(bar.prev_close) if position.market == "A" and bar.prev_close else None
        # 待成交建仓批（T2 限价单；T1 开盘批/T3 触发批为复合判定，由驱动层
        # judge_t1_open/judge_t3 一次性或逐日判定，不在此通用驱动——06 Stage D 口径）
        for tranche in position.tranches:
            if tranche.status != "pending" or tranche.leg in ("T1_open", "T3_trigger"):
                continue
            limit = tranche.limit_price or tranche.ref_price
            fill = (
                match_limit_buy(position.code, tranche.lots, limit, bar)
                if position.market == "A"
                else None  # HK：建仓批同样走收盘复核口径（D8 定谳前）
            )
            if position.market == "HK" and day.is_final:
                fill = match_limit_buy(position.code, tranche.lots, limit, bar)
            fill = gate_fill(fill, bar, band)
            if fill is not None:
                outcome.requests.append(
                    _request(fill, position.code, tranche.leg, tranche.ref_price, ts)
                )
        # 持仓止盈/止损（悲观序）
        if position.lots > 0 and position.stop is not None and position.target1 is not None:
            if position.market == "A":
                from stock_trading_agent.paper.fills import match_bar_pessimistic

                try:
                    fill = match_bar_pessimistic(
                        position.code,
                        lots=position.lots,
                        limit=position.target1,
                        stop=position.stop,
                        bar=bar,
                        slippage=a_share_slippage(fill_price_hint(bar)),
                    )
                except ValueError:
                    fill = None  # 未触发任何单（正常日）
                fill = gate_fill(fill, bar, band)
                if fill is not None:
                    outcome.requests.append(
                        _request(fill, position.code, "stop_or_target", position.stop, ts)
                    )
            elif day.is_final:
                from stock_trading_agent.paper.rules import hk_close_review_match

                try:
                    fill = hk_close_review_match(
                        position.code,
                        lots=position.lots,
                        limit=position.target1,
                        stop=position.stop,
                        close_bar=bar,
                        slippage=hk_slippage(bar.close),
                    )
                except ValueError:
                    fill = None
                if fill is not None:
                    outcome.requests.append(
                        _request(fill, position.code, "stop_or_target", position.stop, ts)
                    )
        outcome.marks[position.code] = bar.close
        _expire_due(position, day.market_date, outcome)
    return outcome


def fill_price_hint(bar: Bar) -> float:
    return bar.close


def _expire_due(position, market_date: str, outcome: EngineOutcome) -> None:
    for tranche in position.tranches:
        if tranche.status == "pending" and tranche.expires_at and tranche.expires_at < market_date:
            outcome.expired_legs.append((position.code, tranche.leg))


def _request(fill: Fill, code: str, leg: str, ref_price: float, ts: str) -> PendingFillRequest:
    from stock_trading_agent.paper.confirm import request_from_fill

    request_id = _request_id(code, leg, fill.date)
    return request_from_fill(fill, request_id=request_id, ref_price=ref_price, ts=ts)


def mark_equity(portfolio: SimPortfolio, market_date: str, marks: dict[str, float]) -> SimPortfolio:
    """追加权益点（同日重跑覆盖同日点）；NAV = equity / budget（13-paper §⑥）。"""
    from stock_trading_agent.paper.model import EquityPoint

    market_value = 0.0
    for position in portfolio.positions:
        price = marks.get(position.code, position.last_price)
        if price is not None:
            position.last_price = price
            position.last_price_date = market_date
            market_value += position.lots * position.board_lot * price
    equity = portfolio.cash + market_value
    point = EquityPoint(
        date=market_date,
        cash=round(portfolio.cash, 2),
        market_value=round(market_value, 2),
        equity=round(equity, 2),
        nav=round(equity / portfolio.budget, 4) if portfolio.budget > 0 else 0.0,
    )
    portfolio.equity_history = [p for p in portfolio.equity_history if p.date != market_date]
    portfolio.equity_history.append(point)
    portfolio.equity_history.sort(key=lambda p: p.date)
    return portfolio


def request_from_t2_event(
    code: str, facts: dict, ts: str, *, request_id: str
) -> PendingFillRequest | None:
    """watch.py t2_pending_fill 台账事件 → 待确认请求（facts 容错：缺 leg/ref_price/lots 返 None）。

    提示口径（low ≤ entry_ref）只承载触发事实；成交判据由用户确认时按穿价口径核对（07 两把尺）。
    """
    leg = facts.get("leg")
    ref_price = facts.get("ref_price") or facts.get("entry_ref")
    lots = facts.get("lots")
    price = facts.get("price")
    if not leg or ref_price is None or not lots or price is None:
        return None
    bar = Bar(
        date=str(facts.get("date", ts[:10])),
        open=float(price),
        high=float(price),
        low=float(price),
        close=float(price),
    )
    return PendingFillRequest(
        request_id=request_id,
        code=code,
        tranche_leg=str(leg),
        side="buy",
        lots=int(lots),
        ref_price=float(ref_price),
        bar=bar,
        ts=ts,
    )
