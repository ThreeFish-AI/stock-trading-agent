"""decisions.jsonl：decision log（sha256 hash 链防篡改，方案 §7/§11/§10⑥）。

`agent:` 前缀是 18:05 postmortem 槽消费的跨仓契约（骨架期只写不消费）；
hash = sha256(canonical(seq, prev_hash, ts, prefix, kind, payload))。
"""

from __future__ import annotations

import hashlib
import json
import threading
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


class ChainBroken(RuntimeError):
    """载入链校验失败（重复 seq / 篡改 / 损坏）——拒载入（W3 fail-loud，I-003）。

    与 store.SchemaVersionMismatch 同纪律：显式报错、不静默降级；修复走 repair-decisions。
    """


def plan_repair(lines: list[DecisionLine]) -> tuple[list[DecisionLine], int]:
    """链修复计划（W2 纯函数）：按 seq 保留首个出现行、丢弃后续重复，重排序号并重算全链 hash。

    坑位：重复行是「同内容重灌」（payload 逐字相同），保首行不丢信息；重排后 seq 连续，
    原 hash/prev_hash 全部作废重算——外仓消费只认 agent: 前缀与 payload，视图不变
    （键控 last-wins，vibe-stock scripts/agent_postmortem.py 已核实）。
    """
    seen: set[int] = set()
    kept: list[DecisionLine] = []
    dropped = 0
    for line in lines:
        if line.seq in seen:
            dropped += 1
            continue
        seen.add(line.seq)
        kept.append(line)
    rebuilt: list[DecisionLine] = []
    prev = GENESIS
    for seq, line in enumerate(kept, start=1):
        fresh = DecisionLine.make(
            seq=seq, ts=line.ts, kind=line.kind, payload=line.payload, prev_hash=prev
        )
        rebuilt.append(fresh)
        prev = fresh.hash
    return rebuilt, dropped


class DecisionLog:
    """内存态链 + 落盘追加；verify_chain() 检测任何篡改 / 删除 / 乱序。

    W1 唯一入口（I-003 根治）：绑定 path 后 append 自动落盘（内存链与盘上链同一行），
    旁路 atomic_append_jsonl 直写自此禁绝。
    """

    def __init__(
        self, lines: list[DecisionLine] | None = None, *, path: Path | None = None
    ) -> None:
        self.lines: list[DecisionLine] = list(lines or [])
        self.path = path  # 绑定落盘路径（None=纯内存，测试/纯函数用）
        # 进程内 append 串行：worker/watcher 线程与 FastAPI 线程池 handler 共享同一实例，
        # seq=len+1 与 append 非原子，交错即产重复 seq（W3 下次启动即 ChainBroken）。
        self._append_lock = threading.Lock()

    @classmethod
    def load(cls, path: Path, *, strict: bool = True) -> DecisionLog:
        """载入即校验（W3）。

        strict=True 校验失败抛 ChainBroken（拒启，先 repair-decisions）；
        strict=False 容错载入（修复工具用）。
        """
        if not path.exists():
            return cls(path=path)
        lines = [
            DecisionLine(**json.loads(raw))
            for raw in path.read_text(encoding="utf-8").splitlines()
            if raw
        ]
        log = cls(lines, path=path)
        if strict and not log.verify_chain():
            raise ChainBroken(
                f"{path}: hash 链校验失败（重复 seq/篡改/损坏）——拒载入，先跑 repair-decisions"
            )
        return log

    @property
    def head_hash(self) -> str:
        return self.lines[-1].hash if self.lines else GENESIS

    def append(
        self, ts: str, kind: str, payload: dict, *, path: Path | None = None
    ) -> DecisionLine:
        """追加一条决策（内存链 + 落盘原子追加；落盘路径 = 显式 path 参数 ?? 实例绑定 path）。

        整体持 _append_lock：seq 计算、内存追加与落盘为一个临界区（跨线程无重复 seq）。
        """
        with self._append_lock:
            line = DecisionLine.make(
                seq=len(self.lines) + 1,
                ts=ts,
                kind=kind,
                payload=payload,
                prev_hash=self.head_hash,
            )
            self.lines.append(line)
            target = path if path is not None else self.path
            if target is not None:
                from stock_trading_agent.state.store import atomic_append_jsonl

                atomic_append_jsonl(target, asdict(line))
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
