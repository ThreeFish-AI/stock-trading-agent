"""record_fill 逐笔确认流（方案 §3 M1：触价只写待确认记录，用户确认后才记账，无人工确认不落账）。

事件源：vibe-stock watch.py 的 t2_pending_fill 台账事件（07 钉死：只推演不写回）——
本模块消费该类事件产出待确认请求；驳回 = 只作废该次请求、批保持 pending、零状态迁移。
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_trading_agent.paper.fills import Bar, Fill

REQUEST_STATES = ("pending", "confirmed", "rejected")


@dataclass(frozen=True)
class PendingFillRequest:
    """一条待确认成交请求（触价产出；state=pending 等人工）。

    fill_price：撮合价（严格穿价 min(open,limit) / 止损 max/min(open,stop)±滑点，方案 §12）；
    引擎撮合产出的请求必带；t2_pending_fill 事件适配的请求不带（提示口径无撮合，确认时按
    参考价语义人工核对，07 两把尺）。落账优先 fill_price（ledger.apply_fill，13-paper §⑤）。
    """

    request_id: str
    code: str
    tranche_leg: str  # T1_open | T2_limit | T3_trigger
    side: str  # buy | sell
    lots: int
    ref_price: float  # 提示口径参考价（entry_ref 等）
    bar: Bar
    ts: str
    state: str = "pending"
    fill_price: float | None = None


class ConfirmError(ValueError):
    """非法确认流迁移（非 pending 不可再确认/驳回）。"""


def request_from_fill(fill: Fill, request_id: str, ref_price: float, ts: str) -> PendingFillRequest:
    """撮合出的候选成交 → 待确认请求（未确认前不进账本；fill_price=撮合价 §12 口径）。"""
    return PendingFillRequest(
        request_id=request_id,
        code=fill.code,
        tranche_leg=fill.tranche_leg or "T2_limit",
        side=fill.side,
        lots=fill.lots,
        ref_price=ref_price,
        bar=Bar(date=fill.date, open=fill.price, high=fill.price, low=fill.price, close=fill.price),
        ts=ts,
        fill_price=fill.price,
    )


def confirm(req: PendingFillRequest) -> PendingFillRequest:
    if req.state != "pending":
        raise ConfirmError(f"请求 {req.request_id} 状态 {req.state} 不可确认（仅 pending）")
    from dataclasses import replace

    return replace(req, state="confirmed")


def reject(req: PendingFillRequest) -> PendingFillRequest:
    """驳回：只作废该次请求（批保持 pending、零状态迁移，07 口径）。"""
    if req.state != "pending":
        raise ConfirmError(f"请求 {req.request_id} 状态 {req.state} 不可驳回（仅 pending）")
    from dataclasses import replace

    return replace(req, state="rejected")
