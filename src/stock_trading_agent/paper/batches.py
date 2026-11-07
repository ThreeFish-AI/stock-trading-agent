"""批状态机与仓位状态机械推导（纯函数）。

口径锚（vibe-stock，不复制文字）：references/07 sim 组合 SSOT 节（0.0.28 钉死）——
任一批成交即 holding；所有存在的批均 expired 且无成交为建仓失败；成交请求被驳回或未确认时
批保持 pending、不产生状态迁移；展示标签不回写 status。references/06 Stage D（T1 跳空回落窗）。
"""

from __future__ import annotations

from stock_trading_agent.paper.model import Position, Tranche


class BatchTransitionError(ValueError):
    """非法批迁移（终态不可逆、两终态互斥）。"""


def fill_tranche(tranche: Tranche, filled_price: float, filled_at: str) -> Tranche:
    """pending → filled（写成交价与时刻；T2/T3 须经用户逐笔确认后进入，见 confirm.py）。"""
    if tranche.status != "pending":
        raise BatchTransitionError(
            f"批 {tranche.leg} 状态 {tranche.status} 不可成交（仅 pending 可）"
        )
    return tranche.model_copy(
        update={"status": "filled", "filled_price": filled_price, "filled_at": filled_at}
    )


def expire_tranche(tranche: Tranche) -> Tranche:
    """pending → expired（仅两来源合法：批自身 GTC 到期；清仓撤单——见 ledger.close_position）。"""
    if tranche.status != "pending":
        raise BatchTransitionError(
            f"批 {tranche.leg} 状态 {tranche.status} 不可过期（仅 pending 可）"
        )
    return tranche.model_copy(update={"status": "expired"})


def derive_position_status(tranches: list[Tranche], *, closed_requested: bool = False) -> str:
    """仓位状态机械推导（07：任一批 filled→holding；全 expired 无成交→closed；否则 pending_open）。

    坑位：拆出 T3 批的多手仓位因 T3 补足不会因到期而失败（07 语义）——T3 窗口内触发按
    收盘补足记 filled（非 expired），该规则属 06/07；本函数只按批终态机械推导。
    closed_requested：用户清仓指令（余批撤单后判定）。
    """
    if closed_requested:
        has_fill = any(t.status == "filled" for t in tranches)
        return (
            "closed"
            if has_fill or not any(t.status == "pending" for t in tranches)
            else "pending_open"
        )
    if any(t.status == "filled" for t in tranches):
        return "holding"
    if tranches and all(t.status == "expired" for t in tranches):
        return "closed"  # 建仓失败：所有存在的批均 expired 且无成交（lots 归 0 由 ledger 负责）
    return "pending_open"


def sync_position(position: Position, *, closed_requested: bool = False) -> Position:
    """以批终态重导仓位 status（机械推导，不信任存量标签；展示标签不回写 status 的反面：
    status 只能由本函数与 ledger 落账改写）。"""
    status = derive_position_status(position.tranches, closed_requested=closed_requested)
    updates: dict = {"status": status}
    if status == "closed" and position.lots:
        updates["lots"] = 0
    return position.model_copy(update=updates)
