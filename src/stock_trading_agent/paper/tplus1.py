"""A 股 T+1 纯函数（E-42/E-48：可卖=持仓−当日买入−在途卖出冻结；日切清零）。

日切清零由 market_date 参数化天然实现（零持久化冻结态）；冻结面裁定（13-paper §③）：
pending 建仓批不占 position.lots（结构性零冻结），在途冻结唯一对应确认队列中 pending
的卖出请求（防两笔并发确认双卖同批手数）。坑位：Operation.trade_date 缺省（旧行）
回落 ts 的 Asia/Shanghai 钟面日期。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from stock_trading_agent.paper.model import Operation, SimPortfolio

_MARKET_TZ = ZoneInfo("Asia/Shanghai")


def _op_trade_date(op: Operation) -> str:
    if op.trade_date:
        return op.trade_date
    try:
        ts = dt.datetime.fromisoformat(op.ts)
    except ValueError:
        return op.ts[:10] if len(op.ts) >= 10 else op.ts
    return ts.astimezone(_MARKET_TZ).strftime("%Y-%m-%d")


def same_day_bought_lots(operations: list[Operation], code: str, market_date: str) -> int:
    """当日买入手数（T+1 扣减面；按 trade_date 钟面日匹配）。"""
    return sum(
        op.lots
        for op in operations
        if op.code == code and op.side == "buy" and _op_trade_date(op) == market_date
    )


def available_lots(
    portfolio: SimPortfolio, code: str, market_date: str, *, pending_sell_lots: int = 0
) -> int:
    """T+1 可卖手数 = position.lots − 当日买入 − 在途卖出冻结（负值夹 0）。

    日切清零：market_date 前移后当日买入面自然归零（无冻结持久化态）。
    """
    position = portfolio.position(code)
    if position is None:
        return 0
    held = position.lots
    bought_today = same_day_bought_lots(portfolio.operations, code, market_date)
    return max(0, held - bought_today - pending_sell_lots)
