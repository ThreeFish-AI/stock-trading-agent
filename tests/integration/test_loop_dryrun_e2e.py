"""Loop dry-run 端到端：事件 → 路由 → dry-run 会话 → 断言 → 落盘全链（tmp 状态目录，零网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.events import EventEnvelope
from stock_trading_agent.loop.loop import AgentLoop, LoopReject
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


def run_one(loop: AgentLoop, kind: str, code: str) -> tuple[QueueState, SessionsState]:
    queue, sessions = QueueState(), SessionsState()
    task = loop.ingest(EventEnvelope(event_id="e1", source="panel", kind=kind, code=code), queue)
    request, result = loop.run_session(task)
    assert loop.assert_and_persist(task, request, result, queue=queue, sessions=sessions) is True
    return queue, sessions


class TestHappyPath:
    def test_full_chain_persists(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue, _sessions = run_one(loop, "diagnose_quick", "600519")

        task = queue.tasks[0]
        assert task.status == "done"
        for path in (
            layout.queue_path,
            layout.sessions_path,
            layout.decisions_path,
            layout.budget_path,
        ):
            assert path.exists(), path

        # 落盘内容回放断言（非仅存在性）：queue done + sessions 登记 + decisions 链 + schema 注入
        persisted_q = QueueState.from_dict(
            json.loads(layout.queue_path.read_text(encoding="utf-8"))
        )
        assert persisted_q.tasks[0].status == "done"
        persisted_s = SessionsState.from_dict(
            json.loads(layout.sessions_path.read_text(encoding="utf-8"))
        )
        assert next(iter(persisted_s.sessions.values())).task_id == task.task_id
        assert persisted_s.total_tokens() == {"input_tokens": 0, "output_tokens": 0}
        chain = DecisionLog.load(layout.decisions_path)
        assert chain.verify_chain() and chain.lines[-1].kind == "task_done"
        budget = json.loads(layout.budget_path.read_text(encoding="utf-8"))
        assert budget["tasks"] == 1

    def test_opinion_request_carries_schema(self, tmp_path: Path):
        # §9 意见段必带 schema（喂 CC 的 --json-schema；NR-3）
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue = QueueState()
        task = loop.ingest(
            EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"), queue
        )
        request, _ = loop.run_session(task)
        assert request.schema is not None and "draft-07" in request.schema.get("$schema", "")
        assert "signal" in request.schema["properties"]

    def test_audit_archive_written_after_gate_with_content(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        run_one(loop, "monitor_review", "600519")
        files = list(layout.audit_dir.rglob("dryrun-*.jsonl"))
        assert files, "断言器放行后应归档转录（NR-3 次序）"
        first = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        assert first["type"] == "system" and first["session_id"]


class TestRefusalPath:
    def _refuse(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue, sessions = QueueState(), SessionsState()
        task = loop.ingest(
            EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"), queue
        )
        request, _ = loop.run_session(task)
        bad = CCResult(
            exit_code=0,
            envelope={"subtype": "success"},
            structured_output=None,
            result_line_present=True,
            transcript_path=Path("/dev/null"),
        )
        ok = loop.assert_and_persist(task, request, bad, queue=queue, sessions=sessions)
        return layout, request, task, sessions, ok

    def test_assert_violation_fails_task_and_records_refuse(self, tmp_path: Path):
        layout, request, task, sessions, ok = self._refuse(tmp_path)
        assert ok is False
        assert task.status == "failed"
        chain = DecisionLog.load(layout.decisions_path)
        assert chain.lines[-1].kind == "assert_refuse"
        assert any("structured_output" in r for r in chain.lines[-1].payload["reasons"])
        assert request.session_uuid not in sessions.sessions  # 违例不登记会话

    def test_refusal_does_not_archive_transcript(self, tmp_path: Path):
        # NR-3 次序：断言违例 → 不归档转录（audit 目录无 dryrun 文件）
        layout, *_ = self._refuse(tmp_path)
        assert not list(layout.audit_dir.rglob("dryrun-*.jsonl"))


class TestGates:
    def test_duplicate_ingest_rejected(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        loop = make_loop(layout)
        queue = QueueState()
        ev = EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519")
        loop.ingest(ev, queue)
        with pytest.raises(LoopReject, match="幂等"):
            loop.ingest(ev, queue)

    def test_lock_precheck_defers(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        layout.agent_dir.mkdir(parents=True, exist_ok=True)
        layout.fetch_lock.touch()
        loop = make_loop(layout)
        with pytest.raises(LoopReject, match=r"fetch_state\.lock"):
            loop.ingest(
                EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"),
                QueueState(),
            )
