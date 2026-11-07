"""状态层：StateLayout 状态布局、原子写与锁、queue/sessions/decisions 台账。

口径锚：docs/research/main-agent-proposal.md §11（agent/ 唯一新写区与读写矩阵）。
"""

from stock_trading_agent.state.paths import StateLayout, WriterRole
from stock_trading_agent.state.store import (
    FileLock,
    atomic_append_jsonl,
    atomic_write_json,
    read_json,
)

__all__ = [
    "FileLock",
    "StateLayout",
    "WriterRole",
    "atomic_append_jsonl",
    "atomic_write_json",
    "read_json",
]
