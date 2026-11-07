"""sim-portfolio.json 数据模型（vibe-stock references/07 P3 schema 的 pydantic 镜像）。

字段名与 07 P3 schema 逐一对应；口径（批语义、两把尺、T1 跳空回落）见 06/07 指针，此处不复制。
sim-portfolio.json 留 ~/.vibe-stock 根（写者=paper 引擎 ledger，方案 §11）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

TRANCHE_KINDS = ("T1_open", "T2_limit", "T3_trigger")
TRANCHE_STATUSES = ("pending", "filled", "expired")
POSITION_STATUSES = ("pending_open", "holding", "closed")


class Tranche(BaseModel):
    """一个建仓批（三批建仓之一，06 Stage D）。"""

    leg: str = Field(pattern=r"^T[123]_(open|limit|trigger)$")
    lots: int = Field(ge=1)
    status: str = Field(default="pending", pattern=r"^(pending|filled|expired)$")
    ref_price: float = Field(gt=0)
    limit_price: float | None = None
    filled_price: float | None = None
    filled_at: str | None = None
    expires_at: str | None = None  # GTC 窗口终点（T1 5 日 / T2 10 日，06 口径）


class Position(BaseModel):
    code: str
    market: str = Field(pattern=r"^(A|HK)$")
    lots: int = Field(default=0, ge=0)
    avg_cost: float = Field(default=0.0, ge=0)
    status: str = Field(default="pending_open", pattern=r"^(pending_open|holding|closed)$")
    stop: float | None = None
    entry_ref: float | None = None
    target1: float | None = None
    high_since_entry: float | None = None
    tranches: list[Tranche] = Field(default_factory=list)


class Operation(BaseModel):
    """确认成交后的台账行（append-only，方案 §12 纪律）。"""

    code: str
    side: str = Field(pattern=r"^(buy|sell)$")
    lots: int = Field(ge=1)
    price: float = Field(gt=0)
    fees: float = Field(default=0.0, ge=0)
    ts: str
    tranche_ref: str | None = None


class SimPortfolio(BaseModel):
    version: int = 1
    updated_at: str = ""
    budget: float = Field(ge=0)
    cash: float = Field(ge=0)
    positions: list[Position] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)

    def position(self, code: str) -> Position | None:
        return next((p for p in self.positions if p.code == code), None)
