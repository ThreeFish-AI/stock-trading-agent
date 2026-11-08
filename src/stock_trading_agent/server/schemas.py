"""提交契约与拒绝映射：pydantic 白名单（六 kind + 码格式）→ HTTP 状态（12-orchestration §⑥）。

面板对 queue.json 是「读+提交命令」（§11 写拓扑）：提交经进程内 LoopRuntime.submit，
写者仍是主 Agent（单一写者纪律）；写动词不直达 state 层。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_trading_agent.loop.loop import RejectReason

# 路由表六 kind 白名单（未知 kind 在此拦截，不进 Loop）
SUBMIT_KINDS: tuple[str, ...] = (
    "diagnose_full",
    "diagnose_quick",
    "monitor_review",
    "batch_wave",
    "paper_confirm",
    "briefing_review",
    "briefing_morning",
    "briefing_evening",
)

KindLiteral = Literal[
    "diagnose_full",
    "diagnose_quick",
    "monitor_review",
    "batch_wave",
    "paper_confirm",
    "briefing_review",
    "briefing_morning",
    "briefing_evening",
]


class SubmitRequest(BaseModel):
    """POST /api/queue/submit 请求体（extra=forbid；code=6 位 A 股或 5 位港股数字码）。"""

    model_config = ConfigDict(extra="forbid")

    kind: KindLiteral
    code: str = Field(pattern=r"^\d{6}$|^\d{5}$")


# RejectReason → HTTP 状态（12-orchestration §⑥ 裁定）
REJECT_STATUS: dict[RejectReason, int] = {
    RejectReason.DUPLICATE: 409,
    RejectReason.LOCK: 423,
    RejectReason.WINDOW: 423,
    RejectReason.UNKNOWN_KIND: 422,
    RejectReason.SCHEMA: 500,
    RejectReason.BUDGET: 500,
    RejectReason.INTERNAL: 500,
}


class CorporateActionRequest(BaseModel):
    """POST /api/paper/corporate-action 请求体（13-paper §④「面板手动馈送」0.0.20 落地）。

    split：ratio_num/ratio_den 全口径比（10送5 → 15/10，须 >1）必填、pay_date/per_share 缺席；
    dividend：per_share_cash>0 与 pay_date 必填。同日除权除息先送转后分红由调用方保序（§④）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["split", "dividend"]
    code: str = Field(pattern=r"^\d{6}$|^\d{5}$")
    ex_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    pay_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    ratio_num: int | None = Field(default=None, gt=0)
    ratio_den: int | None = Field(default=None, gt=0)
    per_share_cash: float | None = Field(default=None, gt=0)
