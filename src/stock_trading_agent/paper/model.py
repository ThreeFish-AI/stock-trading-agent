"""sim-portfolio.json 数据模型（vibe-stock references/07 P3 schema 的 pydantic 镜像）。

SSOT 字段逐一对应 07 P3 schema（positions 含 trailing_armed / high_since_entry 跨日持续态）；
本仓扩展字段显式列举（13-paper §②登记三件义务：docstring 列举+规约登记+默认值保旧文件可载）：
Tranche.limit_price / Tranche.expires_at（GTC 窗口承载）、Operation.fees / Operation.trade_date
（T+1 推导锚）、Position.board_lot / Position.last_price / Position.last_price_date（权益标记）、
SimPortfolio.equity_history / SimPortfolio.corporate_actions（权益曲线与公司行动登记）。
口径（批语义、两把尺、T1 跳空回落）见 06/07 指针，此处不复制。
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
    trailing_armed: bool = False
    high_since_entry: float | None = None
    tranches: list[Tranche] = Field(default_factory=list)
    # 本仓扩展（13-paper §②）：每手股数（A 恒 100；HK 调用方注入）与权益标记价
    # （last_price 只由 paper 引擎在确认 bar 后写入；停牌日沿用旧值=「不计收益」实现面）
    board_lot: int = Field(default=100, ge=1)
    last_price: float | None = None
    last_price_date: str | None = None


class Operation(BaseModel):
    """确认成交后的台账行（append-only，方案 §12 纪律）。"""

    code: str
    side: str = Field(pattern=r"^(buy|sell)$")
    lots: int = Field(ge=1)
    price: float = Field(gt=0)
    fees: float = Field(default=0.0, ge=0)
    ts: str
    tranche_ref: str | None = None
    # 本仓扩展（13-paper §②）：交易日（=确认 bar 之日；T+1 推导锚；旧行缺省回落 ts 钟面日）
    trade_date: str | None = None


class EquityPoint(BaseModel):
    """权益曲线点（append，同日重跑覆盖同日点；13-paper §⑥）。"""

    date: str
    cash: float
    market_value: float
    equity: float
    nav: float = Field(ge=0)


class CorporateActionRecord(BaseModel):
    """公司行动登记（先送转后分红；不产 Operation 行——operations 纯交易流，13-paper §④）。"""

    code: str
    kind: str = Field(pattern=r"^(split|dividend)$")
    ex_date: str
    pay_date: str | None = None
    ratio_num: int | None = None  # split 全口径比（10送5 → 15/10）
    ratio_den: int | None = None
    per_share_cash: float | None = None  # dividend 每股税前现金
    tax: float = Field(default=0.0, ge=0)
    cash_delta: float = Field(default=0.0)
    note: str = ""


class SimPortfolio(BaseModel):
    version: int = 1
    updated_at: str = ""
    budget: float = Field(ge=0)
    cash: float = Field(ge=0)
    positions: list[Position] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)
    # 本仓扩展（13-paper §②）
    equity_history: list[EquityPoint] = Field(default_factory=list)
    corporate_actions: list[CorporateActionRecord] = Field(default_factory=list)

    def position(self, code: str) -> Position | None:
        return next((p for p in self.positions if p.code == code), None)
