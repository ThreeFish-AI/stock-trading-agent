"""allocation 导入（vibe-stock batch/portfolio.py 的 allocation.json 转三批建仓）。

入口契约（06 Stage D 机制 SSOT，本模块零格式发明）：消费 vibe-stock 批量精选产物
`portfolio/allocation.json`（rows：code/name/price/stop/entry_ref/target1/lots/tranches
{T1_open, T2_limit_entryref, T3_trigger}…）；逐 row 建 Position 三批（T1_open ref=信号日
收盘 price、T2_limit ref=limit=entry_ref；T3_trigger 复合触发（站上 target1 / 回踩
MA20±1% / 窗口补足）判据在 engine.judge_t3，06 Stage D）。
纯函数零 IO（读文件由调用方）；写者=paper 引擎（open_position 经 ledger）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from stock_trading_agent.paper.ledger import LedgerError
from stock_trading_agent.paper.model import SimPortfolio, Tranche


class AllocationError(ValueError):
    """allocation 契约违例（缺键/三价不单调/手数非正/市场不可判）。"""


@dataclass(frozen=True)
class AllocationRow:
    """allocation.json 一行 → 一个建仓 Position 的全部输入（呈现态原值，不进决策 payload）。"""

    code: str
    market: str  # A | HK
    lots: int
    price: float  # 信号日收盘（T1 判定锚）
    stop: float | None
    entry_ref: float | None
    target1: float | None
    ma20: float | None
    t1_lots: int
    t2_lots: int
    t3_lots: int


def _market_of(code: str) -> str:
    """市场判定：6 位数字（0/3/6 开头）→ A 股；5 位数字 → HK；其余拒（宁拒不猜）。"""
    if len(code) == 6 and code[0] in "036" and code.isdigit():
        return "A"
    if len(code) == 5 and code.isdigit():
        return "HK"
    raise AllocationError(f"代码 {code} 无法判定市场（6 位 A 股 / 5 位港股）")


def parse_allocation(data: dict) -> list[AllocationRow]:
    """allocation.json dict → 行列表（校验：三价单调、手数≥1、三批手数守恒）。"""
    rows = []
    for raw in data.get("rows") or []:
        code = str(raw.get("code") or "").strip()
        if not code:
            raise AllocationError("allocation 行缺 code")
        market = _market_of(code)
        tranches = raw.get("tranches") or {}
        t1 = int(tranches.get("T1_open") or 0)
        t2 = int(tranches.get("T2_limit_entryref") or 0)
        t3 = int(tranches.get("T3_trigger") or 0)
        lots = int(raw.get("lots") or 0)
        price = float(raw.get("price") or 0)
        if price <= 0 or lots < 1 or t1 + t2 + t3 != lots:
            raise AllocationError(f"{code} 手数不守恒或价格非法：lots={lots} 三批={t1}/{t2}/{t3}")
        stop = raw.get("stop")
        entry_ref = raw.get("entry_ref")
        target1 = raw.get("target1")
        if stop is not None and entry_ref is not None and not float(stop) < float(entry_ref):
            raise AllocationError(f"{code} 三价不单调：stop={stop} ≥ entry_ref={entry_ref}")
        if entry_ref is not None and target1 is not None and not float(entry_ref) <= float(target1):
            raise AllocationError(f"{code} 三价不单调：entry_ref={entry_ref} > target1={target1}")
        rows.append(
            AllocationRow(
                code=code,
                market=market,
                lots=lots,
                price=price,
                stop=None if stop is None else float(stop),
                entry_ref=None if entry_ref is None else float(entry_ref),
                target1=None if target1 is None else float(target1),
                ma20=None if raw.get("ma20") is None else float(raw["ma20"]),
                t1_lots=t1,
                t2_lots=t2,
                t3_lots=t3,
            )
        )
    if not rows:
        raise AllocationError("allocation 无有效行（rows 空）")
    return rows


def import_allocation(
    portfolio: SimPortfolio | None, rows: list[AllocationRow], *, opened_date: str
) -> SimPortfolio:
    """行列表 → 三批建仓落账（新账本以 allocation budget/cash 起始；逐行 open_position）。

    幂等性由调用方（已持仓 code 即 LedgerError 拒绝整批——先全量预检再落，防半批导入）；
    T1 批 ref_price=信号日收盘（t1 判定锚）、T2 批 limit=entry_ref、T3 批 ref=MA20（回踩锚；
    站上 target1/窗口补足由 judge_t3 复合判定，06 Stage D）。
    """
    from stock_trading_agent.paper.ledger import open_position

    if portfolio is None:
        raise LedgerError("账本未初始化：先经 allocation 建 SimPortfolio（budget/cash 起始）")
    for row in rows:
        if portfolio.position(row.code) is not None:
            raise LedgerError(f"标的 {row.code} 已在账本（整批拒绝防半批导入）")
    opened = dt.date.fromisoformat(opened_date)
    # GTC 窗口（06 口径换算，日历日近似交易日报 ESTIMATED）：T2 限价批 10 日 → +14；
    # T1 转限价 5 日 → +7（engine.judge_t1_open）；T3 触发窗 20 日 → +28（paper_eod._t3_window_end）
    t2_expires = (opened + dt.timedelta(days=14)).isoformat()
    for row in rows:
        t2_ref = row.entry_ref or row.price
        t3_ref = row.ma20 or row.entry_ref or row.price
        # 单手股单段建满（06）：零手批不建（Tranche.lots ge=1，构造期即防）
        tranches = [
            Tranche(leg=leg, lots=lots, ref_price=ref, limit_price=limit, expires_at=exp)
            for leg, lots, ref, limit, exp in (
                ("T1_open", row.t1_lots, row.price, None, None),
                ("T2_limit", row.t2_lots, t2_ref, row.entry_ref, t2_expires),
                ("T3_trigger", row.t3_lots, t3_ref, None, None),
            )
            if lots >= 1
        ]
        portfolio = open_position(
            portfolio,
            code=row.code,
            market=row.market,
            tranches=tranches,
            entry_ref=row.entry_ref,
            stop=row.stop,
            target1=row.target1,
        )
        portfolio.positions[-1].opened_date = opened_date
        portfolio.positions[-1].t3_ma20 = row.ma20
    return portfolio


def new_portfolio_from_allocation(data: dict, *, opened_date: str) -> SimPortfolio:
    """allocation.json dict → 全新账本（budget 起始现金；cash_left 不用于 sim——sim 全额可用）。"""
    rows = parse_allocation(data)
    budget = float(data.get("budget") or 0)
    if budget <= 0:
        raise AllocationError("allocation 缺 budget（>0）")
    portfolio = SimPortfolio(version=1, budget=round(budget, 2), cash=round(budget, 2))
    return import_allocation(portfolio, rows, opened_date=opened_date)
