"""LoopRuntime 运行时：资格矩阵、崩溃恢复、消费全链、WAL 先落盘、幂等与原因码。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.loop import LoopReject
from stock_trading_agent.loop.monitor_events import MonitorEvent
from stock_trading_agent.loop.t1 import T1Outcome
from stock_trading_agent.loop.worker import task_eligible
from stock_trading_agent.state.queue import QueueState, TaskRecord

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _ev(code: str = "600519", review: str | None = "止损复核", **kw) -> MonitorEvent:
    base = dict(
        ts="09:31:00",
        code=code,
        market="cn",
        kind="real",
        rule="stop_break",
        level="T0",
        notify=True,
        review=review,
    )
    base.update(kw)
    return MonitorEvent(**base)


class TestTaskEligible:
    def test_locks_defer_any_kind(self):
        from stock_trading_agent.state.queue import TaskRecord as T

        for kind in ("t1_refresh", "diagnose_quick", "monitor_review"):
            task = T(task_id="a", dedup_key="k", kind=kind, code="600519")
            assert task_eligible(
                task, fetch_lock=True, watch_lock=False, now=dt.datetime.now(tz=dt.UTC)
            )
            assert task_eligible(
                task, fetch_lock=False, watch_lock=True, now=dt.datetime.now(tz=dt.UTC)
            )

    def test_report_window_recheck_at_consume(self):
        task = TaskRecord(task_id="a", dedup_key="k", kind="diagnose_quick", code="600519")
        in_win = dt.datetime(2026, 11, 8, 17, 45, tzinfo=SHANGHAI)  # 周日也算窗内（钟面判定）
        out_win = dt.datetime(2026, 11, 8, 19, 0, tzinfo=SHANGHAI)
        assert task_eligible(task, fetch_lock=False, watch_lock=False, now=in_win)
        assert task_eligible(task, fetch_lock=False, watch_lock=False, now=out_win) is None

    def test_non_report_kind_ignores_window(self):
        task = TaskRecord(task_id="a", dedup_key="k", kind="monitor_review", code="600519")
        now = dt.datetime(2026, 11, 8, 17, 45, tzinfo=SHANGHAI)
        assert task_eligible(task, fetch_lock=False, watch_lock=False, now=now) is None


class TestRecoverOrphans:
    def test_running_residual_abandoned_with_decision(self, make_runtime):
        rt = make_runtime()
        q = rt.loop.load_queue().__class__()
        q.submit(TaskRecord(task_id="t1", dedup_key="k1", kind="diagnose_quick", code="600519"))
        q.transition("t1", "running")
        from stock_trading_agent.state.store import atomic_write_json

        atomic_write_json(rt.layout.queue_path, q.to_dict())

        orphans = rt.recover_orphans()
        assert len(orphans) == 1
        reloaded = QueueState.from_dict(
            json.loads(rt.layout.queue_path.read_text(encoding="utf-8"))
        )
        assert reloaded.tasks[0].status == "abandoned"
        assert reloaded.tasks[0].finished_at
        lines = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["kind"] == "task_abandoned"

    def test_no_residual_zero_write(self, make_runtime):
        rt = make_runtime()
        assert rt.recover_orphans() == []
        assert not rt.layout.decisions_path.exists()


class TestConsumeOne:
    def test_full_chain_session_task(self, make_runtime):
        rt = make_runtime()
        rt.submit(kind="diagnose_quick", code="600519")
        task = rt.consume_one()
        assert task is not None and task.status == "done"
        q = QueueState.from_dict(json.loads(rt.layout.queue_path.read_text(encoding="utf-8")))
        assert q.tasks[0].status == "done"
        assert q.tasks[0].finished_at  # 终态时间戳落盘（终态字段完整）
        assert q.tasks[0].started_at  # running 迁移时已写
        assert rt.layout.sessions_path.exists()
        assert rt.layout.budget_path.exists()

    def test_consume_empty_returns_none(self, make_runtime):
        assert make_runtime().consume_one() is None

    def test_session_request_carries_runs_cwd(self, make_runtime):
        # CC 会话 cwd=agent/runs/<session_uuid>/（spawn 前建目录，12-orchestration §④）
        rt = make_runtime()
        queue = QueueState()
        from stock_trading_agent.loop.events import EventEnvelope

        task = rt.loop.ingest(
            EventEnvelope(event_id="e", source="panel", kind="diagnose_quick", code="600519"), queue
        )
        request = rt.loop.build_request(task)
        assert request.cwd is not None
        assert request.cwd.startswith(str(rt.layout.runs_dir))
        from pathlib import Path as _P

        assert _P(request.cwd).is_dir() and request.session_uuid in request.cwd

    def test_lock_present_defers_leaving_queued(self, make_runtime):
        from conftest import hold_lock

        rt = make_runtime()
        rt.submit(kind="diagnose_quick", code="600519")
        rt.layout.agent_dir.mkdir(parents=True, exist_ok=True)
        rt.layout.fetch_lock.touch()  # 陈旧锁文件：不误判
        task = rt.consume_one()
        assert task is not None and task.status == "done"
        rt.submit(kind="diagnose_quick", code="601919")
        with hold_lock(rt.layout.fetch_lock):  # 真实持锁：defer 留 queued
            assert rt.consume_one() is None
            q = QueueState.from_dict(json.loads(rt.layout.queue_path.read_text(encoding="utf-8")))
            assert q.tasks[1].status == "queued"
        task = rt.consume_one()  # 锁释放：自动补跑
        assert task is not None and task.status == "done"

    def test_t1_zero_llm_task_runs_runner(self, make_runtime):
        calls: list[str] = []

        class FakeT1:
            def run(self, code: str, *, workdir: Path) -> T1Outcome:
                calls.append(code)
                workdir.mkdir(parents=True, exist_ok=True)
                (workdir / "context-pack.md").write_text("# pack", encoding="utf-8")
                return T1Outcome(ok=True, workdir=workdir, reasons=())

        rt = make_runtime(t1=FakeT1())
        ev = _ev(code="600519")
        task = rt.record_monitor_event(ev)
        assert task is not None and task.kind == "t1_refresh"
        consumed = rt.consume_one()
        assert consumed is not None and consumed.status == "done"
        assert calls == ["600519"]
        assert rt.layout.runs_dir.name == "runs"  # workdir 布局键在场
        lines = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        kinds = [json.loads(x)["kind"] for x in lines]
        assert "monitor_event" in kinds and "task_submitted" in kinds and "task_done" in kinds

    def test_t1_failure_is_terminal_no_retry(self, make_runtime):
        class FailT1:
            def run(self, code: str, *, workdir: Path) -> T1Outcome:
                return T1Outcome(
                    ok=False, workdir=workdir, reasons=("fetch exit 2：日 K 全链失败",)
                )

        rt = make_runtime(t1=FailT1())
        rt.record_monitor_event(_ev(code="600519"))
        consumed = rt.consume_one()
        assert consumed is not None and consumed.status == "failed"
        # 当日同 code 再触发：find_any 命中终态 → 不再入队（含失败不自动重试）
        assert rt.record_monitor_event(_ev(code="600519")) is None

    def test_assert_violation_fails_task(self, make_runtime):
        class BadCC(DryRunCCClient):
            def spawn_once(self, req):
                super().spawn_once(req)
                return CCResult(
                    exit_code=0,
                    envelope={"subtype": "success"},
                    structured_output=None,  # success 缺 structured_output = 失败
                    result_line_present=True,
                    transcript_path=Path("/dev/null"),
                )

        rt = make_runtime(cc=BadCC())
        rt.submit(kind="diagnose_quick", code="600519")
        task = rt.consume_one()
        assert task is not None and task.status == "failed"
        lines = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["kind"] == "assert_refuse"

    def test_spawn_crash_leaves_running_on_disk_recoverable(self, make_runtime):
        class CrashCC(DryRunCCClient):
            def spawn_once(self, req):
                raise RuntimeError("进程中途被杀")

        rt = make_runtime(cc=CrashCC())
        rt.submit(kind="diagnose_quick", code="600519")
        with pytest.raises(RuntimeError):
            rt.consume_one()
        q = QueueState.from_dict(json.loads(rt.layout.queue_path.read_text(encoding="utf-8")))
        assert q.tasks[0].status == "running"  # WAL：崩溃残留可回收
        rt2 = make_runtime(root=rt.layout.root)
        assert len(rt2.recover_orphans()) == 1


class TestSubmitAndWatch:
    def test_submit_duplicate_reason(self, make_runtime):
        rt = make_runtime()
        rt.submit(kind="diagnose_quick", code="600519")
        with pytest.raises(LoopReject) as exc_info:
            rt.submit(kind="diagnose_quick", code="600519")
        assert exc_info.value.reason == "duplicate"

    def test_monitor_event_per_day_per_code_once(self, make_runtime):
        rt = make_runtime()
        assert rt.record_monitor_event(_ev(code="600519")) is not None
        assert rt.record_monitor_event(_ev(code="600519")) is None  # 已在队（非终态也拒）
        assert rt.record_monitor_event(_ev(code="601919")) is not None

    def test_suppressed_event_logged_but_no_task(self, make_runtime):
        rt = make_runtime()
        assert rt.record_monitor_event(_ev(code="600519", suppress_reason="cooldown")) is None
        lines = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["kind"] == "monitor_event"

    def test_watcher_offset_persisted_across_instances(self, make_runtime):
        from stock_trading_agent.loop.monitor_events import market_today

        rt = make_runtime()
        events_file = rt.layout.monitor_events_file(market_today())
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(
            json.dumps(
                {
                    "ts": "09:31:00",
                    "code": "600519",
                    "market": "cn",
                    "kind": "real",
                    "rule": "stop_break",
                    "level": "T0",
                    "notify": True,
                    "review": "止损复核",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        from stock_trading_agent.loop.watcher import EventsWatcher

        def _append_event(ts: str) -> None:
            events_file.open("a", encoding="utf-8").write(
                json.dumps(
                    {
                        "ts": ts,
                        "code": "600519",
                        "market": "cn",
                        "kind": "real",
                        "rule": "stop_break",
                        "level": "T0",
                        "notify": True,
                        "review": "止损复核",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        w1 = EventsWatcher(layout=rt.layout, on_events=lambda evs: None)
        assert len(w1.poll_once()) == 1
        _append_event("09:32:00")
        assert len(w1.poll_once()) == 1  # 同实例二次消费：偏移必须推进并落盘（W6 别名回归钉）
        w2 = EventsWatcher(layout=rt.layout, on_events=lambda evs: None)  # 新实例（模拟重启）
        assert w2.poll_once() == []  # 偏移断点：不重复消费
        _append_event("09:33:00")
        assert len(w2.poll_once()) == 1

    def test_watcher_thread_picks_up_new_event(self, make_runtime):
        import threading

        from stock_trading_agent.loop.monitor_events import market_today

        rt = make_runtime()
        events_file = rt.layout.monitor_events_file(market_today())
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text("", encoding="utf-8")
        got = threading.Event()

        def on_events(evs):
            if evs:
                got.set()

        from stock_trading_agent.loop.watcher import EventsWatcher

        w = EventsWatcher(layout=rt.layout, on_events=on_events)
        w.start()
        try:
            events_file.write_text(
                json.dumps(
                    {
                        "ts": "09:32:00",
                        "code": "600519",
                        "market": "cn",
                        "kind": "real",
                        "rule": "stop_break",
                        "level": "T0",
                        "notify": True,
                        "review": "止损复核",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            import time

            deadline = time.monotonic() + 10
            while not got.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            w.stop()
        assert got.is_set()


class TestDecisionChainIntegrity:
    """0.0.15 决策链完整性：空链直写（W1）、游标双写（W2）、重放历史（W3）防回退。"""

    def _read_lines(self, rt) -> list[dict]:
        import json as _json

        path = rt.layout.decisions_path
        if not path.exists():
            return []
        return [_json.loads(r) for r in path.read_text(encoding="utf-8").splitlines() if r]

    def test_submit_without_start_continues_chain(self, make_runtime):
        """W1 端到端：runtime A（未 acquire，降级态）提交后，全新 runtime B 提交不得回退 seq。"""
        from stock_trading_agent.state.decisions import DecisionLog

        a = make_runtime()
        a.submit(kind="diagnose_quick", code="600519")
        b = make_runtime(root=a.layout.root)  # 新进程形态：空内存 DecisionLog
        b.submit(kind="diagnose_quick", code="000001")
        lines = self._read_lines(b)
        assert [x["seq"] for x in lines] == [1, 2]  # 无 seq 回退、无双写
        assert DecisionLog.load(b.layout.decisions_path).verify_chain()

    def test_full_task_lifecycle_no_duplicate_lines(self, make_runtime):
        """W2 端到端：submit + 消费 + 面板侧决策（l2_toggled 路径模拟）后行数恰等于决策条数。"""
        from stock_trading_agent.state.decisions import DecisionLog

        rt = make_runtime()
        rt.submit(kind="diagnose_quick", code="600519")
        rt.loop.decisions.append(  # 面板旁路追加形态（l2_toggle/briefing_ack 同入口）
            "2026-11-08T00:00:00+00:00",
            "briefing_ack",
            {"date": "2026-11-08", "code": "600519"},
            path=rt.layout.decisions_path,
        )
        task = rt.consume_one()
        assert task is not None and task.status == "done"
        lines = self._read_lines(rt)
        seqs = [x["seq"] for x in lines]
        assert seqs == list(range(1, len(lines) + 1))  # 连续且唯一（旧 W2 会复制已落盘行）
        assert DecisionLog.load(rt.layout.decisions_path).verify_chain()

    def test_restart_acquire_no_history_replay(self, make_runtime):
        """W3 端到端：A 消费后 B acquire_daemon 再消费，无历史块重追加。"""
        from stock_trading_agent.state.decisions import DecisionLog

        a = make_runtime()
        a.submit(kind="diagnose_quick", code="600519")
        a.consume_one()
        n_after_a = len(self._read_lines(a))
        b = make_runtime(root=a.layout.root)
        assert b.acquire_daemon()
        b.submit(kind="diagnose_quick", code="000001")
        b.consume_one()
        lines = self._read_lines(b)
        # B 的两段（submit+done）恰 +2 行；旧 W3 会把 A 的全部历史重追加
        assert len(lines) == n_after_a + 2
        assert DecisionLog.load(b.layout.decisions_path).verify_chain()

    def test_acquire_daemon_on_broken_chain_degrades(self, make_runtime, tmp_path):
        """strict load：断链文件上 acquire 返回 False 且 chain_error 可见（worker 不启动）。"""
        import json as _json
        from dataclasses import asdict

        from stock_trading_agent.state.decisions import DecisionLog

        rt = make_runtime()
        rt.submit(kind="diagnose_quick", code="600519")
        orphan = DecisionLog()
        orphan.append("t", "task_done", {"task_id": "z"})
        with rt.layout.decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(asdict(orphan.lines[0]), ensure_ascii=False) + "\n")
        b = make_runtime(root=rt.layout.root)
        assert not b.acquire_daemon()
        assert b.chain_error and "repair-decisions" in b.chain_error
        assert b.start() is False  # worker/watcher 不启动
