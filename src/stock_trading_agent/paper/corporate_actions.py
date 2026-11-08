"""公司行动：先送转（Decimal 重算）后分红（FIFO 分档红利税）——E-43（rqalpha 先例）。

纪律（13-paper §④）：同日除权除息先 apply_split 再 apply_dividend（调用方保序）；
不产 Operation 行（operations 纯交易流，公司行动单列 corporate_actions 登记）；
送转不足 1 股部分按现金找零入 cash（estimated 裁定）；持股期 FIFO 从 operations 推导。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from stock_trading_agent.paper.model import CorporateActionRecord, Operation, SimPortfolio

ESTIMATED = True  # 税档阈值与现金找零口径 estimated（E-43 社区先例；自家样本校准后固化）


@dataclass(frozen=True)
class SplitAction:
    code: str
    ratio_num: int  # 全口径比（10送5 → 15/10）
    ratio_den: int
    ex_date: str


@dataclass(frozen=True)
class DividendAction:
    code: str
    per_share_cash: float  # 每股税前现金
    ex_date: str
    pay_date: str


@dataclass(frozen=True)
class LotBasis:
    trade_date: str
    lots: int


def fifo_lots(operations: list[Operation], code: str, *, as_of: str) -> list[LotBasis]:
    """FIFO 持仓基（买入加、卖出 FIFO 减；as_of（含）之前的行参与推导）。"""
    basis: list[LotBasis] = []
    for op in operations:
        if op.code != code or (op.trade_date or op.ts[:10]) > as_of:
            continue
        if op.side == "buy":
            basis.append(LotBasis(trade_date=op.trade_date or op.ts[:10], lots=op.lots))
        else:
            remaining = op.lots
            while remaining > 0 and basis:
                head = basis[0]
                if head.lots > remaining:
                    basis[0] = LotBasis(trade_date=head.trade_date, lots=head.lots - remaining)
                    remaining = 0
                else:
                    remaining -= head.lots
                    basis.pop(0)
    return [b for b in basis if b.lots > 0]


def dividend_tax_rate(holding_days: int) -> float:
    """A 股分档红利税（E-43）：持股 ≤1 月 20%、≤1 年 10%、>1 年免。"""
    if holding_days <= 30:
        return 0.20
    if holding_days <= 365:
        return 0.10
    return 0.0


def _days(from_date: str, to_date: str) -> int:
    a = dt.date.fromisoformat(from_date)
    b = dt.date.fromisoformat(to_date)
    return (b - a).days


def apply_split(portfolio: SimPortfolio, action: SplitAction) -> SimPortfolio:
    """送转：Decimal 重算 lots×ratio 与 avg_cost÷ratio；tranches 价位字段同除权。

    不足 1 股部分按现金找零入 cash（estimated：按 avg_cost 折现）；建仓批 lots 同步放大
    （pending 批按新股数兑现、filled 批已入仓位）。
    """
    from decimal import ROUND_DOWN, Decimal

    position = portfolio.position(action.code)
    if position is None or position.lots <= 0:
        return portfolio
    ratio = Decimal(action.ratio_num) / Decimal(action.ratio_den)
    new_lots_exact = Decimal(position.lots) * ratio
    new_lots = int(new_lots_exact.to_integral_value(rounding=ROUND_DOWN))
    fraction = new_lots_exact - Decimal(new_lots)
    cash_back = float(fraction * Decimal(position.board_lot) * Decimal(position.avg_cost))
    position.lots = new_lots
    if position.avg_cost > 0:
        position.avg_cost = float(Decimal(str(position.avg_cost)) / ratio)
    scale = float(ratio)
    for tranche in position.tranches:
        tranche.lots = max(1, int(Decimal(tranche.lots) * ratio))
        for field in ("ref_price", "limit_price", "filled_price"):
            value = getattr(tranche, field)
            if value is not None:
                setattr(tranche, field, round(value / scale, 4))
    if position.stop is not None:
        position.stop = round(position.stop / scale, 4)
    if position.entry_ref is not None:
        position.entry_ref = round(position.entry_ref / scale, 4)
    if position.target1 is not None:
        position.target1 = round(position.target1 / scale, 4)
    portfolio.cash += cash_back
    portfolio.corporate_actions.append(
        CorporateActionRecord(
            code=action.code,
            kind="split",
            ex_date=action.ex_date,
            ratio_num=action.ratio_num,
            ratio_den=action.ratio_den,
            cash_delta=round(cash_back, 2),
            note=f"送转 {action.ratio_num}:{action.ratio_den}；不足 1 股现金找零（estimated）",
        )
    )
    return portfolio


def apply_dividend(portfolio: SimPortfolio, action: DividendAction) -> SimPortfolio:
    """分红：FIFO 持股期分档税；付息日 cash += Σ lots×board_lot×per_share×(1−rate)。"""
    position = portfolio.position(action.code)
    if position is None or position.lots <= 0:
        return portfolio
    basis = fifo_lots(portfolio.operations, action.code, as_of=action.ex_date)
    gross = 0.0
    tax = 0.0
    covered = 0
    for lot in basis:
        take = min(lot.lots, position.lots - covered)
        if take <= 0:
            break
        days = _days(lot.trade_date, action.pay_date)
        rate = dividend_tax_rate(days)
        gross += take * position.board_lot * action.per_share_cash
        tax += take * position.board_lot * action.per_share_cash * rate
        covered += take
    if covered < position.lots:  # 基不齐（如送转后无对应买行）：剩余按最重税档保守计
        rate = 0.20
        rest = position.lots - covered
        gross += rest * position.board_lot * action.per_share_cash
        tax += rest * position.board_lot * action.per_share_cash * rate
    net = round(gross - tax, 2)
    portfolio.cash += net
    portfolio.corporate_actions.append(
        CorporateActionRecord(
            code=action.code,
            kind="dividend",
            ex_date=action.ex_date,
            pay_date=action.pay_date,
            per_share_cash=action.per_share_cash,
            tax=round(tax, 2),
            cash_delta=net,
            note="FIFO 分档红利税（estimated）",
        )
    )
    return portfolio


def apply_corporate_action(
    portfolio: SimPortfolio, action: SplitAction | DividendAction
) -> SimPortfolio:
    """单入口；同日除权除息的先送转后分红由调用方保序（E-43）。"""
    if isinstance(action, SplitAction):
        return apply_split(portfolio, action)
    return apply_dividend(portfolio, action)
