"""decisions.jsonl：decision log（sha256 hash 链防篡改，方案 §7/§11/§10⑥）。

`agent:` 前缀是 18:05 postmortem 槽消费的跨仓契约（骨架期只写不消费）；
hash = sha256(canonical(seq, prev_hash, ts, prefix, kind, payload))。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

PREFIX = "agent:"  # 跨仓消费契约：vibe-stock 18:05 postmortem 槽按此前缀拾取


def _canonical(seq: int, prev_hash: str, ts: str, prefix: str, kind: str, payload: dict) -> str:
    return json.dumps(
        [seq, prev_hash, ts, prefix, kind, payload],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_hash(seq: int, prev_hash: str, ts: str, prefix: str, kind: str, payload: dict) -> str:
    return hashlib.sha256(
        _canonical(seq, prev_hash, ts, prefix, kind, payload).encode("utf-8")
    ).hexdigest()


@dataclass
class DecisionLine:
    seq: int
    ts: str
    prefix: str  # 恒为 agent:
    kind: str
    payload: dict
    prev_hash: str
    hash: str

    @classmethod
    def make(cls, seq: int, ts: str, kind: str, payload: dict, prev_hash: str) -> DecisionLine:
        return DecisionLine(
            seq=seq,
            ts=ts,
            prefix=PREFIX,
            kind=kind,
            payload=payload,
            prev_hash=prev_hash,
            hash=compute_hash(seq, prev_hash, ts, PREFIX, kind, payload),
        )


GENESIS = "0" * 64


class DecisionLog:
    """内存态链 + 落盘追加；verify_chain() 检测任何篡改 / 删除 / 乱序。"""

    def __init__(self, lines: list[DecisionLine] | None = None) -> None:
        self.lines: list[DecisionLine] = list(lines or [])

    @classmethod
    def load(cls, path: Path) -> DecisionLog:
        if not path.exists():
            return cls()
        lines = [
            DecisionLine(**json.loads(raw))
            for raw in path.read_text(encoding="utf-8").splitlines()
            if raw
        ]
        log = cls(lines)
        log.verify_chain()  # 载入即校验（读侧防线）
        return log

    @property
    def head_hash(self) -> str:
        return self.lines[-1].hash if self.lines else GENESIS

    def append(
        self, ts: str, kind: str, payload: dict, *, path: Path | None = None
    ) -> DecisionLine:
        """追加一条决策（内存链 + 可选落盘原子追加）。"""
        line = DecisionLine.make(
            seq=len(self.lines) + 1, ts=ts, kind=kind, payload=payload, prev_hash=self.head_hash
        )
        self.lines.append(line)
        if path is not None:
            from stock_trading_agent.state.store import atomic_append_jsonl

            atomic_append_jsonl(path, asdict(line))
        return line

    def verify_chain(self) -> bool:
        """全链校验：序号单调、prev_hash 链接一致、逐行 hash 复算一致；任何破坏返回 False。"""
        prev = GENESIS
        for expect_seq, line in enumerate(self.lines, start=1):
            if line.seq != expect_seq or line.prev_hash != prev:
                return False
            if line.prefix != PREFIX:
                return False
            if line.hash != compute_hash(
                line.seq, line.prev_hash, line.ts, line.prefix, line.kind, line.payload
            ):
                return False
            prev = line.hash
        return True
