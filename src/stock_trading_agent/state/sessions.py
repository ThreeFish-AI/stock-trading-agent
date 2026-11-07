"""sessions.json：CC 会话登记（方案 §11：uuid / 转录路径 / 用量 / 成本）。

订阅轨双口径（F-34）：token 数 + 等效美元两列同记；等效值由调用方按其计费形态给。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SessionRecord:
    session_uuid: str
    task_id: str
    transcript_path: str
    usage_tokens: dict[str, int] = field(default_factory=dict)
    cost_usd_equiv: float = 0.0
    ts: str = ""


@dataclass
class SessionsState:
    version: int = 1
    sessions: dict[str, SessionRecord] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> SessionsState:
        if data is None:
            return cls()
        return cls(
            version=data["version"],
            sessions={uuid: SessionRecord(**rec) for uuid, rec in data["sessions"].items()},
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "sessions": {uuid: asdict(rec) for uuid, rec in self.sessions.items()},
        }

    def upsert(self, rec: SessionRecord) -> None:
        self.sessions[rec.session_uuid] = rec

    def total_cost_usd_equiv(self) -> float:
        return sum(rec.cost_usd_equiv for rec in self.sessions.values())

    def total_tokens(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for rec in self.sessions.values():
            for key, val in rec.usage_tokens.items():
                totals[key] = totals.get(key, 0) + val
        return totals
