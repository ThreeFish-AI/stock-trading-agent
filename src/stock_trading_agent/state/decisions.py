"""decisions.jsonl：decision log（sha256 hash 链防篡改，方案 §7/§11/§10⑥）。

`agent:` 前缀是 18:05 postmortem 槽消费的跨仓契约（骨架期只写不消费）；
hash = sha256(canonical(seq, prev_hash, ts, prefix, kind, payload))。

链完整性裁定（0.0.15，12-orchestration §④）：文件是 SSOT、内存是缓存——任何落盘追加
必经 `append(path=...)` 单一入口，在 `<stem>.lock` 内做尾部对账（内存空/落后即采纳文件态、
超前即拒 fail-closed）；`load()` 收严为 strict（断链/坏行 raise `DecisionChainError`，
修复走 CLI `repair-decisions` 一次性保行重链）。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

PREFIX = "agent:"  # 跨仓消费契约：vibe-stock 18:05 postmortem 槽按此前缀拾取

REPAIR_HINT = "先运行 uv run stock-trading-agent repair-decisions 修复决策链"


class DecisionChainError(RuntimeError):
    """决策链断裂/不一致（strict load 与写侧尾部对账共用；fail-closed，修复走 CLI）。"""


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


@dataclass(frozen=True)
class RepairStats:
    """repair_chain_lines 摘要（CLI 打印与测试锚；零敏感量）。"""

    rows_before: int
    rows_after: int
    breaks: list[int]
    old_sha256: str
    new_head: str


def _parse_lines(raw_lines: list[str], *, origin: str) -> list[DecisionLine]:
    """逐行解析（坏行即 raise——不删行纪律下无降级路径）。"""
    lines: list[DecisionLine] = []
    for i, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        try:
            lines.append(DecisionLine(**json.loads(raw)))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DecisionChainError(f"{origin}: 第 {i} 行不可解析：{exc}") from exc
    return lines


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
    def load(cls, path: Path) -> DecisionLog:
        """strict 载入（0.0.15 收严）：断链/坏行 raise，不再静默续写。"""
        if not path.exists():
            return cls()
        lines = _parse_lines(path.read_text(encoding="utf-8").splitlines(), origin=str(path))
        log = cls(lines)
        if not log.verify_chain():
            raise DecisionChainError(
                f"{path}: 决策链断裂（首个断点第 {log.first_break()} 行）——{REPAIR_HINT}"
            )
        return log

    @property
    def head_hash(self) -> str:
        return self.lines[-1].hash if self.lines else GENESIS

    def append(
        self, ts: str, kind: str, payload: dict, *, path: Path | None = None
    ) -> DecisionLine:
        """追加一条决策。

        path=None：纯内存形态（selftest/纯函数用例，不落盘）。
        path 在场：唯一落盘入口——`<stem>.lock` 内尾部对账后原子追加（文件是 SSOT）：
        内存空/落后 → 采纳文件态续链（他进程已追加的降级态/重启态）；
        内存超前于文件 → raise（疑似回滚/篡改，fail-closed）；
        等长但尾行 hash 不一致 → raise（文件与内存分叉）。
        """
        if path is None:
            line = DecisionLine.make(
                seq=len(self.lines) + 1, ts=ts, kind=kind, payload=payload, prev_hash=self.head_hash
            )
            self.lines.append(line)
            return line
        from stock_trading_agent.state.store import FileLock, atomic_append_jsonl

        lock_path = path.parent / f"{path.stem}.lock"  # decisions.jsonl → decisions.lock
        with FileLock(lock_path):
            file_lines = _parse_lines(
                path.read_text(encoding="utf-8").splitlines() if path.exists() else [],
                origin=str(path),
            )
            file_log = DecisionLog(file_lines)
            if not file_log.verify_chain():
                raise DecisionChainError(
                    f"{path}: 决策链断裂（首个断点第 {file_log.first_break()} 行）——{REPAIR_HINT}"
                )
            if len(self.lines) > len(file_lines):
                raise DecisionChainError(
                    f"{path}: 内存链（{len(self.lines)} 行）超前于文件（{len(file_lines)} 行）"
                    "——疑似文件回滚/篡改，拒绝追加"
                )
            if len(self.lines) < len(file_lines):
                self.lines = file_lines  # 内存落后：采纳文件态（他进程/重启后已追加）
            elif file_lines and self.lines[-1] != file_lines[-1]:
                raise DecisionChainError(f"{path}: 内存尾行与文件尾行不一致（等长分叉）——拒绝追加")
            line = DecisionLine.make(
                seq=len(file_lines) + 1,
                ts=ts,
                kind=kind,
                payload=payload,
                prev_hash=file_lines[-1].hash if file_lines else GENESIS,
            )
            atomic_append_jsonl(path, asdict(line))
            self.lines.append(line)
        return line

    def first_break(self) -> int:
        """首个违例行的 1-based 序号（verify_chain()=False 时诊断用；完好返回 0）。"""
        prev = GENESIS
        for expect_seq, line in enumerate(self.lines, start=1):
            if (
                line.seq != expect_seq
                or line.prev_hash != prev
                or line.prefix != PREFIX
                or line.hash
                != compute_hash(
                    line.seq, line.prev_hash, line.ts, line.prefix, line.kind, line.payload
                )
            ):
                return expect_seq
            prev = line.hash
        return 0

    def verify_chain(self) -> bool:
        """全链校验：序号单调、prev_hash 链接一致、逐行 hash 复算一致；任何破坏返回 False。"""
        return self.first_break() == 0


def repair_chain_lines(
    raw_lines: list[str], *, ts: str, old_sha256: str
) -> tuple[list[dict], RepairStats]:
    """一次性保行重链（纯函数，判据与 IO 分离；CLI repair-decisions 消费）。

    按文件原序保留每行 ts/prefix/kind/payload，重算 seq=1..N 与 prev/hash；
    链尾追加 chain_repair_started（rows_before/old_sha256/breaks/tool）与
    chain_repair_done（rows_after）两条决策；不删任何行（备份/坏行中止由 IO 壳负责）。
    payload 全为计数与哈希，零敏感量（NR-5）。
    """
    kept = _parse_lines(raw_lines, origin="repair")
    breaks: list[int] = []
    prev = GENESIS
    for i, obj in enumerate(kept, start=1):
        if obj.seq != i or obj.prev_hash != prev:
            breaks.append(i)
        prev = obj.hash or prev
    rebuilt: list[DecisionLine] = []
    for obj in kept:
        line = DecisionLine.make(
            seq=len(rebuilt) + 1,
            ts=obj.ts,
            kind=obj.kind,
            payload=obj.payload,
            prev_hash=rebuilt[-1].hash if rebuilt else GENESIS,
        )
        rebuilt.append(line)
    started = DecisionLine.make(
        seq=len(rebuilt) + 1,
        ts=ts,
        kind="chain_repair_started",
        payload={
            "rows_before": len(kept),
            "old_sha256": old_sha256,
            "breaks": breaks,
            "tool": "cli:repair-decisions",
        },
        prev_hash=rebuilt[-1].hash if rebuilt else GENESIS,
    )
    done = DecisionLine.make(
        seq=len(rebuilt) + 2,
        ts=ts,
        kind="chain_repair_done",
        payload={"rows_after": len(kept) + 2},
        prev_hash=started.hash,
    )
    rows = [asdict(line) for line in [*rebuilt, started, done]]
    return rows, RepairStats(
        rows_before=len(kept),
        rows_after=len(rows),
        breaks=breaks,
        old_sha256=old_sha256,
        new_head=done.hash,
    )
