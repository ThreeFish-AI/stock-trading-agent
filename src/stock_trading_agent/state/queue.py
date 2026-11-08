"""queue.json：任务队列 + WAL 状态机 + 幂等键（方案 §11；崩溃断点重放语义 §14）。

状态机：queued → running → done | failed | abandoned（终态不可回）；
幂等键：同 dedup_key 非终态在队即拒重复提交。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

TERMINAL_STATES = frozenset({"done", "failed", "abandoned"})
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "abandoned"}),
    "running": frozenset({"done", "failed", "abandoned"}),
    "done": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}


class QueueError(ValueError):
    """非法状态迁移 / 幂等冲突。"""


@dataclass
class TaskRecord:
    task_id: str
    dedup_key: str
    kind: str
    code: str
    status: str = "queued"
    enqueued_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    session_uuid: str | None = None
    error: str | None = None


@dataclass
class QueueState:
    version: int = 1
    tasks: list[TaskRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> QueueState:
        if data is None:
            return cls()
        return cls(
            version=data["version"],
            tasks=[TaskRecord(**t) for t in data["tasks"]],
        )

    def to_dict(self) -> dict:
        return {"version": self.version, "tasks": [asdict(t) for t in self.tasks]}

    def find_active(self, dedup_key: str) -> TaskRecord | None:
        """同 dedup_key 非终态任务在队即返回之（幂等键判据）。"""
        for t in self.tasks:
            if t.dedup_key == dedup_key and t.status not in TERMINAL_STATES:
                return t
        return None

    def find_any(self, dedup_key: str) -> TaskRecord | None:
        """同 dedup_key 任意状态（含终态）在队即返回之。

        T1 每股每日一次判据位：t1_refresh:<code>:<date> 当日已 done/failed 也不再触发
        （含失败不自动重试，12-orchestration §③）。
        """
        return next((t for t in self.tasks if t.dedup_key == dedup_key), None)

    def submit(self, task: TaskRecord) -> TaskRecord:
        """入队；同 dedup_key 非终态在队 → 拒（幂等键，方案 §10①）。"""
        if self.find_active(task.dedup_key) is not None:
            raise QueueError(f"幂等冲突：dedup_key={task.dedup_key} 非终态任务已在队")
        self.tasks.append(task)
        return task

    def transition(self, task_id: str, new_status: str) -> TaskRecord:
        """WAL 状态迁移：非法迁移（含终态回退）即拒。"""
        task = next((t for t in self.tasks if t.task_id == task_id), None)
        if task is None:
            raise QueueError(f"任务不存在：{task_id}")
        if new_status not in VALID_TRANSITIONS.get(task.status, frozenset()):
            raise QueueError(f"非法迁移：{task.status} → {new_status}（task_id={task_id}）")
        task.status = new_status
        return task

    def replayables(self) -> list[TaskRecord]:
        """崩溃重启后的断点重放集（§14 台账幂等断点重放）：running 残留按 abandoned 重放。"""
        return [t for t in self.tasks if t.status == "running"]
