"""Loop dry-run 端到端：事件 → 路由 → dry-run 会话 → 断言 → 落盘全链（tmp 状态目录，零网络）。"""

from __future__ import annotations

from pathlib import Path

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.events import EventEnvelope
from stock_trading_agent.loop.loop import AgentLoop
from stock_trading_agent.loop.router import load_default_table
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import QueueState
from stock_trading_agent.state.sessions import SessionsState


def make_loop(layout: StateLayout) -> AgentLoop:
    return AgentLoop(
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        layout=layout,
        table=load_default_table(),
        decisions=DecisionLog(),
    )


class TestHappyPath:
    def test_full_chain_persists(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue, sessions = QueueState(), SessionsState()

        task = loop.ingest(
            EventEnvelope(event_id="e1", source="panel", kind="diagnose_quick", code="600519"),
            queue,
        )
        request, result = loop.run_session(task)
        assert request.prompt == "/vibe-stock 600519"
        assert (
            loop.assert_and_persist(task, request, result, queue=queue, sessions=sessions) is True
        )

        # 状态落盘断言：queue done + sessions 登记 + decisions 链
        assert task.status == "done"
        assert (
            layout.queue_path.exists()
            and layout.sessions_path.exists()
            and layout.decisions_path.exists()
        )
        persisted_q = QueueState.from_dict(
            __import__("json").loads(layout.queue_path.read_text(encoding="utf-8"))
        )
        assert persisted_q.tasks[0].status == "done"
        assert sessions.sessions[request.session_uuid].task_id == task.task_id
        chain = DecisionLog.load(layout.decisions_path)
        assert chain.verify_chain() and chain.lines[-1].kind == "task_done"

    def test_audit_archive_populated(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue, sessions = QueueState(), SessionsState()
        task = loop.ingest(
            EventEnvelope(event_id="e", source="panel", kind="monitor_review", code="600519"), queue
        )
        request, result = loop.run_session(task)
        loop.assert_and_persist(task, request, result, queue=queue, sessions=sessions)
        assert any(layout.audit_dir.rglob("dryrun-*.jsonl"))


class TestRefusalPath:
    def test_assert_violation_fails_task_and_records_refuse(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue, sessions = QueueState(), SessionsState()
        task = loop.ingest(
            EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"), queue
        )
        request, _ = loop.run_session(task)

        # 注入断言违例：success 但 structured_output 缺失（F-14 形态）
        bad = CCResult(
            exit_code=0,
            envelope={"subtype": "success"},
            structured_output=None,
            result_line_present=True,
            transcript_path=Path("/dev/null"),
        )
        assert loop.assert_and_persist(task, request, bad, queue=queue, sessions=sessions) is False
        assert task.status == "failed"
        chain = DecisionLog.load(layout.decisions_path)
        assert chain.lines[-1].kind == "assert_refuse"
        assert any("structured_output" in r for r in chain.lines[-1].payload["reasons"])
        assert request.session_uuid not in sessions.sessions  # 违例不登记会话


class TestGates:
    def test_duplicate_ingest_rejected(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue = QueueState()
        ev = EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519")
        loop.ingest(ev, queue)
        try:
            loop.ingest(ev, queue)
            raise AssertionError("幂等键未拦截")
        except Exception as exc:
            assert "幂等" in str(exc) or "dedup" in str(exc)

    def test_lock_precheck_defers(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        layout.agent_dir.mkdir(parents=True, exist_ok=True)
        layout.fetch_lock.touch()
        loop = make_loop(layout)
        try:
            loop.ingest(
                EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"),
                QueueState(),
            )
            raise AssertionError("锁前置未拦截")
        except Exception as exc:
            assert "fetch_state.lock" in str(exc)
