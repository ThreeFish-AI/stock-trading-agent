"""确认成交落账唯一入口（sim-portfolio.json 写者 = paper 引擎，方案 §11）。

**NR-2（无条件全局规则）**：模拟盘永不接真实下单接口——含券商 API 直连与委托报文任何形态；
本模块（及整个 paper 包）不得 import 任何网络库 / 券商接口（红线扫描测试为机器门）。
持久化只经 state.store.atomic_write_json 落 sim-portfolio.json（原子写，无半写态）。
涨跌停三态与停牌闸在 paper.rules（撮合后置过滤）；T+1 卖出闸在本模块（available_lots，
13-paper §③）；公司行动在 paper.corporate_actions。
"""

from __future__ import annotations

import datetime as _dt

from stock_trading_agent.paper.batches import fill_tranche, sync_position
from stock_trading_agent.paper.confirm import PendingFillRequest
from stock_trading_agent.paper.fees import calc_fees
from stock_trading_agent.paper.model import Operation, Position, SimPortfolio, Tranche
from stock_trading_agent.paper.tplus1 import available_lots
from stock_trading_agent.state.store import atomic_write_json


class LedgerError(ValueError):
    """落账契约违例（未确认请求 / 批不匹配 / 现金不足）。"""


def _now() -> str:
    return _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds")


def apply_fill(
    portfolio: SimPortfolio, req: PendingFillRequest, *, pending_sell_lots: int = 0
) -> SimPortfolio:
    """一笔 **已确认** 请求落账（不可变更新：返回新 SimPortfolio，不改入参）。

    做法：批 pending→filled、avg_cost 手数加权重算、cash 扣减（含费用）、operations append、
    仓位状态机械重导；坑位：req.state 非 confirmed 即拒（无人工确认不落账）；
    卖出过 T+1 闸（可卖=持仓−当日买入−在途卖出冻结，E-42/E-48，13-paper §③）——
    pending_sell_lots 由调用方传确认队列在途冻结（排除自身请求后）。
    """
    if req.state != "confirmed":
        raise LedgerError(f"请求 {req.request_id} 未确认（state={req.state}），无人工确认不落账")
    position = portfolio.position(req.code)
    if position is None:
        raise LedgerError(f"标的 {req.code} 不在账本（无仓位可落）")

    notional = req.lots * position.board_lot * req.ref_price  # A 股 board_lot=100；HK 调用方注入
    fees = calc_fees(position.market, req.side, notional)

    if req.side == "buy":
        # 买入 = 建仓批成交：必须命中对应 pending 批（三批建仓全是买方批，06 Stage D）
        tranche = next(
            (t for t in position.tranches if t.leg == req.tranche_leg and t.status == "pending"),
            None,
        )
        if tranche is None:
            raise LedgerError(f"标的无待成交批：{req.code} {req.tranche_leg}")
        new_tranche = fill_tranche(tranche, filled_price=req.ref_price, filled_at=req.ts or _now())
        new_tranches = [new_tranche if t is tranche else t for t in position.tranches]
        total_lots = position.lots + req.lots
        new_cost = (position.avg_cost * position.lots + req.ref_price * req.lots) / total_lots
        cash = portfolio.cash - notional - fees
        if cash < 0:
            raise LedgerError(f"现金不足：{portfolio.cash} < {notional + fees:.2f}")
        new_position = position.model_copy(
            update={"tranches": new_tranches, "lots": total_lots, "avg_cost": round(new_cost, 4)}
        )
        new_position = sync_position(new_position)
        tranche_ref: str | None = req.tranche_leg
    else:
        # 卖出 = 仓位级清仓/减仓：不占用建仓批（tranche_ref=None）；清零即清仓（07：清仓→closed）
        market_date = req.bar.date
        sellable = available_lots(
            portfolio, req.code, market_date, pending_sell_lots=pending_sell_lots
        )
        if sellable < req.lots:
            raise LedgerError(
                f"可卖手数不足（T+1 口径：持仓 {position.lots} − 当日买入 − 在途冻结 = {sellable}"
                f" < {req.lots}，E-42/E-48）"
            )
        cash = portfolio.cash + notional - fees
        remaining = position.lots - req.lots
        new_position = position.model_copy(update={"lots": remaining})
        new_position = sync_position(new_position, closed_requested=remaining == 0)
        tranche_ref = None

    op = Operation(
        code=req.code,
        side=req.side,
        lots=req.lots,
        price=req.ref_price,
        fees=fees,
        ts=req.ts or _now(),
        tranche_ref=tranche_ref,
        trade_date=req.bar.date,  # T+1 推导锚（确认时点≠交易日，13-paper §③）
    )
    return portfolio.model_copy(
        update={
            "cash": round(cash, 2),
            "updated_at": _now(),
            "positions": [new_position if p is position else p for p in portfolio.positions],
            "operations": [*portfolio.operations, op],
        }
    )


def close_position(portfolio: SimPortfolio, code: str) -> SimPortfolio:
    """清仓撤单：余 pending 批全部 expired，仓位机械重导（07：清仓撤单是 expired 第二来源）。"""
    position = portfolio.position(code)
    if position is None:
        raise LedgerError(f"标的 {code} 不在账本")
    new_tranches = [
        t if t.status != "pending" else t.model_copy(update={"status": "expired"})
        for t in position.tranches
    ]
    new_position = sync_position(
        position.model_copy(update={"tranches": new_tranches}), closed_requested=True
    )
    return portfolio.model_copy(
        update={
            "updated_at": _now(),
            "positions": [new_position if p is position else p for p in portfolio.positions],
        }
    )


def persist(portfolio: SimPortfolio, path) -> None:
    """唯一持久化口（原子写 sim-portfolio.json；路径来自 StateLayout.sim_portfolio_path）。"""
    atomic_write_json(path, portfolio.model_dump())


def load(path) -> SimPortfolio | None:
    from stock_trading_agent.state.store import read_json

    data = read_json(path, expect_version=1)
    return SimPortfolio.model_validate(data) if data is not None else None


def open_position(
    portfolio: SimPortfolio,
    code: str,
    market: str,
    tranches: list[Tranche],
    *,
    entry_ref: float | None = None,
    stop: float | None = None,
    target1: float | None = None,
) -> SimPortfolio:
    """新建仓位（allocation 导入后建三批，06 Stage D；批默认 pending）。"""
    if portfolio.position(code) is not None:
        raise LedgerError(f"标的 {code} 已在账本")
    position = Position(
        code=code, market=market, tranches=tranches, entry_ref=entry_ref, stop=stop, target1=target1
    )
    return portfolio.model_copy(
        update={"positions": [*portfolio.positions, position], "updated_at": _now()}
    )
