"""Loop 运行时（M1 接线）：queue 消费者线程 + monitor 监听接线 + 崩溃恢复（方案 §10①、§14）。

单消费者不变式：agent/daemon.lock 长持有 = 唯一有权消费 queue 的运行时实例
（server worker 线程或 CLI run-once 二选一）；queue.json 一切读-改-写 =
agent/queue.lock 内「重载→变更→落盘」；worker 执行 spawn/T1 子进程期间零锁持有；
单 worker 线程串行 CC spawn（D-71 OAuth 竞争防护）。
崩溃恢复：启动期 running 残留 → abandoned + task_abandoned 决策（M1 人在环不自动重放，
M3 预留 replayables 消费方）。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import threading
import traceback
from dataclasses import asdict

from stock_trading_agent.budget.gate import BudgetConfig
from stock_trading_agent.cc_client.contract import CCClient
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.advice import (
    MONITOR_REVIEW_KIND,
    count_t2_done,
    t2_admission,
    universe_real_codes,
)
from stock_trading_agent.loop.events import (
    EventEnvelope,
    gate_report_event,
    lock_is_held,
    lock_precheck,
)
from stock_trading_agent.loop.loop import AgentLoop, LoopReject
from stock_trading_agent.loop.monitor_events import MonitorEvent, market_today, t1_trigger
from stock_trading_agent.loop.router import load_default_table
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.watcher import EventsWatcher
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import TaskRecord
from stock_trading_agent.state.store import (
    FileLock,
    LockTimeout,
    atomic_append_jsonl,
    atomic_write_json,
)

T1_KIND = "t1_refresh"  # zero_llm 分派键（routing.yaml zero_llm.t1_refresh → diagnose_prepare）
T1_HANDLER = "diagnose_prepare"


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds")


def task_eligible(
    task: TaskRecord, *, fetch_lock: bool, watch_lock: bool, now: _dt.datetime
) -> str | None:
    """消费期资格复检（与 ingest 闸同源，判据只收严）：None=可执行，str=defer 理由。

    锁在场（盯盘/取数中）→ defer 留 queued 不阻塞队头；report 类 kind 禁跑窗复检。
    """
    if reason := lock_precheck(fetch_lock=fetch_lock, watch_lock=watch_lock):
        return reason
    if reason := gate_report_event(now, task.kind):
        return reason
    return None


class LoopRuntime:
    """面板/CLI 共用的运行时：提交半边入口 + 后台消费 + T1 接线 + 崩溃恢复。"""

    def __init__(
        self,
        *,
        layout: StateLayout,
        cc: CCClient | None = None,
        table=None,
        budget: BudgetConfig | None = None,
        t1: T1Runner | None = None,
        decisions: DecisionLog | None = None,
        poll_interval_s: float = 2.0,
        watcher_enabled: bool = True,
    ) -> None:
        self.layout = layout
        self.table = table or load_default_table()
        self.cc = cc if cc is not None else DryRunCCClient(audit_dir=layout.audit_dir)
        self.t1 = t1 if t1 is not None else T1Runner()
        self.loop = AgentLoop(
            cc=self.cc,
            layout=layout,
            table=self.table,
            decisions=decisions or DecisionLog(),
            budget=budget,
        )
        self.poll_interval_s = poll_interval_s
        self.watcher_enabled = watcher_enabled
        self._stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._watcher: EventsWatcher | None = None
        self._daemon_stack: contextlib.ExitStack | None = None

    # --- 单消费者锁 ---
    @property
    def is_active(self) -> bool:
        """daemon 锁持有 ∧ worker 线程存活（面板只读降级态可见）。"""
        return (
            self._daemon_stack is not None
            and self._worker_thread is not None
            and self._worker_thread.is_alive()
        )

    def acquire_daemon(self, *, timeout_s: float = 3.0) -> bool:
        """获取 daemon.lock（失败返回 False——serve 降级只读，run-once 由 CLI 转 exit 3）。"""
        if self._daemon_stack is not None:
            return True
        try:
            stack = contextlib.ExitStack()
            stack.enter_context(FileLock(self.layout.daemon_lock, timeout_s=timeout_s))
        except LockTimeout:
            return False
        self._daemon_stack = stack
        # 决策链载入即校验（单写者=本运行时，内存链与盘上链一致）
        self.loop.decisions = DecisionLog.load(self.layout.decisions_path)
        return True

    def start(self) -> bool:
        """启动运行时：daemon 锁 → 崩溃恢复 → worker/watcher 线程。"""
        if not self.acquire_daemon():
            return False
        self.recover_orphans()
        self._stop.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name="sta-loop-worker", daemon=True
        )
        self._worker_thread.start()
        if self.watcher_enabled:
            self._watcher = EventsWatcher(layout=self.layout, on_events=self._on_monitor_events)
            self._watcher.start()
        return True

    def stop(self, *, timeout_s: float = 10.0) -> None:
        """停机：停轮询 → join 线程 → 释放 daemon 锁。

        在跑任务不强杀（残留 running 由下次启动回收）。"""
        self._stop.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout_s)
            self._worker_thread = None
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._daemon_stack is not None:
            self._daemon_stack.close()
            self._daemon_stack = None

    # --- 决策追加（须在 queue.lock 持有期内调用；内存链+盘上原子追加同一行） ---
    def _decide(self, kind: str, payload: dict) -> None:
        line = self.loop.decisions.append(_now_iso(), kind, payload)
        atomic_append_jsonl(self.layout.decisions_path, asdict(line))

    # --- 提交半边（面板 POST / CLI run-once --kind 入口；写者=主 Agent 进程内直调） ---
    def submit(self, *, kind: str, code: str, source: str = "panel") -> TaskRecord:
        event = EventEnvelope(
            event_id=f"{source}:{kind}:{code}", source=source, kind=kind, code=code
        )
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            task = self.loop.ingest(event, queue)
            atomic_write_json(self.layout.queue_path, queue.to_dict())
            self._decide(
                "task_submitted",
                {"task_id": task.task_id, "kind": kind, "code": code, "source": source},
            )
        return task

    # --- T1 接线（watcher 回调；T0=登记 only，通知唯一通道是 watch.py osascript，R7） ---
    def _on_monitor_events(self, events: list[MonitorEvent]) -> None:
        for ev in events:
            self.record_monitor_event(ev)

    def record_monitor_event(self, ev: MonitorEvent) -> TaskRecord | None:
        """T0 事件登记 + T1 触发入队（每股每日一次）+ t2_pending_fill 入确认队列。"""
        date = market_today()
        task: TaskRecord | None = None
        if ev.rule == "t2_pending_fill":
            self._enqueue_t2_pending(ev)
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            self._decide(
                "monitor_event",
                {
                    "ts": ev.ts,
                    "code": ev.code,
                    "rule": ev.rule,
                    "level": ev.level,
                    "review": ev.review,
                    "suppress_reason": ev.suppress_reason,
                },
            )
            if t1_trigger(ev):
                key = f"{T1_KIND}:{ev.code}:{date}"
                if queue.find_any(key) is None:
                    now = _dt.datetime.now(tz=_dt.UTC)
                    task = TaskRecord(
                        task_id=f"{key}:{now.strftime('%H%M%S')}",
                        dedup_key=key,
                        kind=T1_KIND,
                        code=ev.code,
                        enqueued_at=_now_iso(),
                    )
                    queue.submit(task)
                    atomic_write_json(self.layout.queue_path, queue.to_dict())
                    self._decide(
                        "task_submitted",
                        {
                            "task_id": task.task_id,
                            "kind": T1_KIND,
                            "code": ev.code,
                            "source": "monitor",
                        },
                    )
        return task

    def _enqueue_t2_pending(self, ev: MonitorEvent) -> None:
        """t2_pending_fill 事件 → paper 确认队列（触价只写待确认记录，07 只推演不写回）。

        sim.lock 串行 pending 读-改-写；payload 零数量（NR-5：决策台账只记 code/leg/id）。
        """
        from stock_trading_agent.paper import pending as paper_pending
        from stock_trading_agent.paper.engine import request_from_t2_event

        ts = ev.ts or _now_iso()
        salt = abs(hash((ev.code, ev.ts, ev.rule))) % 10_000
        request_id = f"pf-{ev.code}-t2-{market_today()}-{salt}"
        req = request_from_t2_event(ev.code, dict(ev.facts), ts, request_id=request_id)
        if req is None:
            return  # facts 缺字段（容错：不产半截请求）
        with FileLock(self.layout.sim_lock):
            queue = paper_pending.load(self.layout.sim_pending_fills_path)
            queue.enqueue(req)
            paper_pending.save(queue, self.layout.sim_pending_fills_path)
        self._decide(
            "paper_pending_fill",
            {"code": ev.code, "leg": req.tranche_leg, "request_id": request_id},
        )

    # --- 崩溃恢复（启动期；running 残留 → abandoned） ---
    def recover_orphans(self) -> list[TaskRecord]:
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            orphans = queue.replayables()
            for task in orphans:
                queue.transition(task.task_id, "abandoned")
                task.finished_at = _now_iso()
                task.error = "进程重启回收 running 残留（M1 人在环不自动重放）"
                self._decide(
                    "task_abandoned",
                    {"task_id": task.task_id, "kind": task.kind, "code": task.code},
                )
            if orphans:
                atomic_write_json(self.layout.queue_path, queue.to_dict())
        return orphans

    # --- 消费（worker 线程与 run-once 共用；两段式锁：锁内选task+running落盘，锁外执行） ---
    def consume_one(self) -> TaskRecord | None:
        """消费恰一个任务；队空/全部 defer 返回 None。"""
        now = _dt.datetime.now(tz=_dt.UTC)
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            picked: TaskRecord | None = None
            for task in queue.tasks:
                if task.status != "queued":
                    continue
                reason = task_eligible(
                    task,
                    fetch_lock=lock_is_held(self.layout.fetch_lock),
                    watch_lock=lock_is_held(self.layout.watch_lock),
                    now=now,
                )
                if reason is not None:
                    continue  # defer：留 queued，不阻塞队头，下一轮再查
                picked = task
                break
            if picked is None:
                return None
            self.loop.mark_running(picked, queue)  # WAL 不变式：执行前 running 落盘
            request = None
            if picked.kind == MONITOR_REVIEW_KIND:
                # T2 准入闸（消费期判定：每日 ≤5 票 + 实盘优先；超额拒=failed 终态防自旋）
                reason = t2_admission(
                    picked,
                    done_count=count_t2_done(queue, today=market_today()),
                    real_codes=universe_real_codes(self.layout),
                )
                if reason is not None:
                    queue.transition(picked.task_id, "failed")
                    picked.finished_at = _now_iso()
                    picked.error = reason
                    self._decide(
                        "advice_refused",
                        {
                            "task_id": picked.task_id,
                            "kind": picked.kind,
                            "code": picked.code,
                            "origin": "gate",
                            "reason_code": (
                                "t2_daily_quota"
                                if "5 票" in reason
                                else "real_priority_universe_unreadable"
                            ),
                            "reasons": [reason],
                        },
                    )
                    atomic_write_json(self.layout.queue_path, queue.to_dict())
                    return picked
            if self.table.zero_llm.get(picked.kind) != T1_HANDLER:
                try:
                    request = self.loop.build_request(picked)
                except LoopReject as exc:  # 消费期拒绝（预算闸等）→ failed 终态，防自旋
                    queue.transition(picked.task_id, "failed")
                    picked.finished_at = _now_iso()
                    picked.error = str(exc)
                    self._decide(
                        "task_failed",
                        {
                            "task_id": picked.task_id,
                            "kind": picked.kind,
                            "code": picked.code,
                            "reasons": [str(exc)],
                        },
                    )
                    atomic_write_json(self.layout.queue_path, queue.to_dict())
                    return picked
        # 锁外执行（spawn / T1 子进程期间零锁持有）
        if request is not None:
            return self._execute_session(picked, request)
        return self._execute_t1(picked, now)

    def _execute_session(self, task: TaskRecord, request) -> TaskRecord:
        result = self.cc.spawn_once(request)
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            sessions = self.loop.load_sessions()
            self.loop.assert_and_persist(task, request, result, queue=queue, sessions=sessions)
        return self._fresh_task(task)

    def _execute_t1(self, task: TaskRecord, now: _dt.datetime) -> TaskRecord:
        workdir = self.layout.t1_workdir(market_today(now), task.code)
        outcome = self.t1.run(task.code, workdir=workdir)
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            current = next(t for t in queue.tasks if t.task_id == task.task_id)  # 重载后副本
            if outcome.ok:
                queue.transition(current.task_id, "done")
                current.finished_at = _now_iso()
                self._decide(
                    "task_done",
                    {
                        "task_id": current.task_id,
                        "kind": current.kind,
                        "code": current.code,
                        "workdir": str(workdir),
                    },
                )
            else:
                queue.transition(current.task_id, "failed")
                current.finished_at = _now_iso()
                current.error = "；".join(outcome.reasons)
                self._decide(
                    "task_failed",
                    {
                        "task_id": current.task_id,
                        "kind": current.kind,
                        "code": current.code,
                        "reasons": list(outcome.reasons),
                    },
                )
            atomic_write_json(self.layout.queue_path, queue.to_dict())
        return current

    def _fresh_task(self, task: TaskRecord) -> TaskRecord:
        """重载队列取终态副本（盘上状态权威；返回对象不滞后）。"""
        queue = self.loop.load_queue()
        return next((t for t in queue.tasks if t.task_id == task.task_id), task)

    # --- worker 线程体 ---
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self.consume_one()
            except Exception:  # 消费异常不杀线程（守护线程死亡=编排停摆）
                traceback.print_exc()
                task = None
            if task is None:
                self._stop.wait(self.poll_interval_s)
