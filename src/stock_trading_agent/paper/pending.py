"""确认队列持久化：~/.vibe-stock/sim-pending-fills.json（根级；写者=paper 引擎）。

落位裁定（13-paper §⑤）：不进 sim-portfolio.json（07 P3 schema 的 positions/operations
纯交易面不掺瞬态）也不进 agent/（编排区不掺领域态）；队列是可重建瞬态非 append-only
台账；与 sim-portfolio.json 同写者同目录。frozen_sell_lots 供 T+1 在途冻结面。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stock_trading_agent.paper.confirm import PendingFillRequest
from stock_trading_agent.paper.confirm import confirm as _confirm
from stock_trading_agent.paper.confirm import reject as _reject
from stock_trading_agent.paper.fills import Bar
from stock_trading_agent.state.store import atomic_write_json, read_json

QUEUE_VERSION = 1


@dataclass
class PendingQueue:
    requests: list[PendingFillRequest] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict

        rows = []
        for r in self.requests:
            row = asdict(r)  # 嵌套 dataclass（bar）随 asdict 展开为 dict
            rows.append(row)
        return {"version": QUEUE_VERSION, "requests": rows}

    @classmethod
    def from_dict(cls, data: dict | None) -> PendingQueue:
        if data is None:
            return cls()
        requests = []
        for row in data.get("requests", []):
            raw = dict(row)
            if isinstance(raw.get("bar"), dict):
                raw["bar"] = Bar(**raw["bar"])
            requests.append(PendingFillRequest(**raw))
        return cls(requests=requests)

    def find(self, request_id: str) -> PendingFillRequest | None:
        return next((r for r in self.requests if r.request_id == request_id), None)

    def enqueue(self, req: PendingFillRequest) -> PendingQueue:
        if self.find(req.request_id) is None:
            self.requests.append(req)
        return self

    def _mutate(self, request_id: str, fn) -> PendingQueue:
        self.requests = [fn(r) if r.request_id == request_id else r for r in self.requests]
        return self

    def confirm(self, request_id: str) -> PendingQueue:
        return self._mutate(request_id, _confirm)

    def reject(self, request_id: str) -> PendingQueue:
        return self._mutate(request_id, _reject)

    def remove(self, request_id: str) -> PendingQueue:
        self.requests = [r for r in self.requests if r.request_id != request_id]
        return self

    def confirmed(self, request_id: str) -> PendingFillRequest:
        req = self.find(request_id)
        if req is None or req.state != "confirmed":
            raise KeyError(f"确认请求不存在或未确认：{request_id}")
        return req

    def frozen_sell_lots(self, code: str) -> int:
        """在途卖出冻结面（state==pending ∧ side==sell；T+1 扣减项，13-paper §③）。"""
        return sum(
            r.lots
            for r in self.requests
            if r.code == code and r.side == "sell" and r.state == "pending"
        )


def load(path: Path) -> PendingQueue:
    data = read_json(path, expect_version=QUEUE_VERSION)
    return PendingQueue.from_dict(data)


def save(queue: PendingQueue, path: Path) -> None:
    atomic_write_json(path, queue.to_dict())
