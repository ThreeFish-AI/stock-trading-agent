"""queue WAL 状态机与幂等键。"""

from __future__ import annotations

import pytest

from stock_trading_agent.state.queue import QueueError, QueueState, TaskRecord


def rec(task_id: str, dedup_key: str = "diagnose_quick:600519:2026-11-07") -> TaskRecord:
    return TaskRecord(task_id=task_id, dedup_key=dedup_key, kind="diagnose_quick", code="600519")


class TestIdempotency:
    def test_same_dedup_active_rejected(self):
        q = QueueState()
        q.submit(rec("a"))
        with pytest.raises(QueueError, match="幂等"):
            q.submit(rec("b"))

    def test_terminal_allows_resubmit_next_day(self):
        q = QueueState()
        q.submit(rec("a"))
        q.transition("a", "running")
        q.transition("a", "done")
        q.submit(rec("b", dedup_key="diagnose_quick:600519:2026-11-08"))  # 新日新键
        assert len(q.tasks) == 2


class TestWalTransitions:
    def test_happy_path(self):
        q = QueueState()
        q.submit(rec("a"))
        q.transition("a", "running")
        q.transition("a", "done")
        assert q.tasks[0].status == "done"

    def test_illegal_transitions_rejected(self):
        # running→running（自环）、done→running（终态回退）、failed→queued（终态回退）均拒
        for start, move in (
            ("running", "running"),
            ("done", "running"),
            ("failed", "queued"),
            ("abandoned", "running"),
        ):
            q = QueueState()
            q.submit(rec("a"))
            if start == "running":
                q.transition("a", "running")
            elif start in ("done", "failed", "abandoned"):
                q.transition("a", "running")
                q.transition("a", start)
            with pytest.raises(QueueError):
                q.transition("a", move)

    def test_queued_can_abandon_directly(self):
        q = QueueState()
        q.submit(rec("a"))
        q.transition("a", "abandoned")
        assert q.tasks[0].status == "abandoned"

    def test_unknown_task_rejected(self):
        with pytest.raises(QueueError):
            QueueState().transition("ghost", "running")

    def test_replayables_after_crash(self):
        q = QueueState()
        q.submit(rec("a"))
        q.submit(rec("b", dedup_key="k:2"))
        q.transition("a", "running")  # 崩溃残留
        assert [t.task_id for t in q.replayables()] == ["a"]
