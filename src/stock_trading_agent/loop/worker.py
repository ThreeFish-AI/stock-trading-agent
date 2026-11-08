"""Loop 运行时（M1 接线）：queue 消费者线程 + monitor 监听接线 + 崩溃恢复（方案 §10①、§14）。

单消费者不变式：agent/daemon.lock 长持有 = 唯一有权消费 queue 的运行时实例
（server worker 线程或 CLI run-once 二选一）；queue.json 一切读-改-写 =
agent/queue.lock 内「重载→变更→落盘」；worker 执行 spawn/T1 子进程期间零锁持有；
单 worker 线程串行 CC spawn（D-71 OAuth 竞争防护）。
崩溃恢复：启动期 running 残留 → abandoned + task_abandoned 决策（monitor 源按 R5 计划重放
plan_replays——§⑪ 0.0.10 落地；panel/cli 源维持 abandoned 人在环语义）。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import threading
import traceback
from dataclasses import dataclass

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
from stock_trading_agent.loop.loop import AgentLoop, LoopReject, RejectReason
from stock_trading_agent.loop.monitor_events import (
    MonitorEvent,
    market_today,
    t1_trigger,
    t2_worthy,
)
from stock_trading_agent.loop.router import load_default_table
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.watcher import EventsWatcher
from stock_trading_agent.state.agent_config import AgentConfig, load_agent_config, save_agent_config
from stock_trading_agent.state.decisions import DecisionChainError, DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import TaskRecord
from stock_trading_agent.state.store import (
    FileLock,
    LockTimeout,
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


@dataclass(frozen=True)
class ReplayAction:
    """重放计划项（plan_replays 纯函数输出；判据与 IO 分离）。"""

    task_id: str
    dedup_key: str
    kind: str
    code: str
    skip_reason: str | None = None  # l2_disabled | replay_cap；None=重放


def plan_replays(
    orphans: list[TaskRecord], queue_tasks: list[TaskRecord], *, l2_enabled: bool
) -> list[ReplayAction]:
    """R5 判据纯函数：monitor 源残留 → 重放（monitor_review 另需 l2；t1 无条件）。

    防自旋：queue_tasks 里同 dedup_key 计数 ≥2 即 replay_cap（含本批 abandoned 的原任务）。
    """
    from collections import Counter

    key_counts = Counter(t.dedup_key for t in queue_tasks)
    actions: list[ReplayAction] = []
    for task in orphans:
        if task.source != "monitor":
            continue  # panel/cli 源维持 abandoned（人在环，M1 语义）
        base = dict(task_id=task.task_id, dedup_key=task.dedup_key, kind=task.kind, code=task.code)
        if task.kind == MONITOR_REVIEW_KIND and not l2_enabled:
            actions.append(ReplayAction(**base, skip_reason="l2_disabled"))
            continue
        if key_counts.get(task.dedup_key, 0) >= 2:
            actions.append(ReplayAction(**base, skip_reason="replay_cap"))
            continue
        actions.append(ReplayAction(**base))
    return actions


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
            decisions=decisions or DecisionLog(path=layout.decisions_path),  # W1 绑定唯一落盘口
            budget=budget,
        )
        self.poll_interval_s = poll_interval_s
        self.watcher_enabled = watcher_enabled
        self.chain_error: str | None = None  # strict load 断链时承载降级原因（面板可见）
        self._last_leak_scan = 0.0
        self._last_auto_briefing_scan = 0.0
        self._pgrep_runner = None  # 测试注入位（None=真跑 pgrep）
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
        """获取 daemon.lock（失败返回 False——serve 降级只读，run-once 由 CLI 转 exit 3）。

        决策链 strict 载入（0.0.15）：断链即释放锁、记 chain_error、返回 False——
        worker/watcher 不启动（防在断链文件上续写），修复走 CLI repair-decisions；
        坑位：失败路径 decisions 重绑纯内存链（降级防毒）——降级期任一 append 不得向
        已有多行文件写 seq=1/GENESIS 毒行（repair 后再毒化、下次启动再次拒载）。
        """
        if self._daemon_stack is not None:
            return True
        try:
            stack = contextlib.ExitStack()
            stack.enter_context(FileLock(self.layout.daemon_lock, timeout_s=timeout_s))
        except LockTimeout:
            self.loop.decisions = DecisionLog()  # 降级防毒：内存链，append 不落盘
            return False
        # strict 载入：断链拒载入（fail-closed），修复走 CLI repair-decisions
        try:
            self.loop.decisions = DecisionLog.load(self.layout.decisions_path)
        except DecisionChainError as exc:
            self.chain_error = str(exc)
            stack.close()  # 释放 daemon 锁（降级只读，不占锁）
            return False
        self.chain_error = None
        self._daemon_stack = stack
        return True

    def start(self) -> bool:
        """启动运行时：daemon 锁 → 崩溃恢复 → paper 孤儿清理 → 泄漏巡检 → worker/watcher 线程。"""
        if not self.acquire_daemon():
            return False
        self.recover_orphans()
        self.purge_confirmed_orphans()  # W5：确认流崩溃窗口脏态启动自愈
        self.scan_cc_leaks()  # D-08：启动先扫一次（孤儿检测 log-only）
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

    # --- 决策追加（须在 queue.lock 持有期内调用；append(path=...) 单一入口：锁内尾部对账+落盘） ---
    def _decide(self, kind: str, payload: dict) -> None:
        self.loop.decisions.append(_now_iso(), kind, payload, path=self.layout.decisions_path)

    # --- 提交半边（面板 POST / CLI run-once --kind 入口；写者=主 Agent 进程内直调） ---
    def submit(self, *, kind: str, code: str, source: str = "panel") -> TaskRecord:
        event = EventEnvelope(
            event_id=f"{source}:{kind}:{code}", source=source, kind=kind, code=code
        )
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            task = self.loop.ingest(event, queue)
            task.source = source
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
                        source="monitor",
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
            # M3 auto-T2（events 台账驱动，12-orchestration §⑩）：
            # R3 直建 TaskRecord——不走 loop.ingest（lock_precheck 在 watch.lock 持有中恒拒）
            # 也不嵌 self.submit（本函数已持 queue.lock，flock 会自死锁）；锁/窗消费期 defer。
            if t2_worthy(ev) and load_agent_config(self.layout.agent_config_path).l2_enabled:
                key = f"{MONITOR_REVIEW_KIND}:{ev.code}:{date}"
                if queue.find_any(key) is None:
                    now = _dt.datetime.now(tz=_dt.UTC)
                    t2_task = TaskRecord(
                        task_id=f"{key}:{now.strftime('%H%M%S')}",
                        dedup_key=key,
                        kind=MONITOR_REVIEW_KIND,
                        code=ev.code,
                        enqueued_at=_now_iso(),
                        source="monitor",
                    )
                    queue.submit(t2_task)
                    atomic_write_json(self.layout.queue_path, queue.to_dict())
                    self._decide(
                        "task_submitted",
                        {
                            "task_id": t2_task.task_id,
                            "kind": MONITOR_REVIEW_KIND,
                            "code": ev.code,
                            "source": "monitor",
                            "trigger": {"rule": ev.rule, "review": ev.review},
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

    # --- L2 配置联动（R1：单开关联动预算闸 + probe 持久化 billing_mode） ---
    def apply_l2_config(self, *, probe: bool = False) -> AgentConfig:
        """读 config → budget.enabled=l2_enabled；probe=True 时探测认证态并持久化。

        probe 判不准/失败一律 "api" 收紧（D2）；决策留痕（l2_toggled 由调用方记，此处记 l2_probe）。
        """
        from stock_trading_agent.budget.gate import billing_mode_from_probe

        cfg = load_agent_config(self.layout.agent_config_path)
        if probe and cfg.l2_enabled:
            mode: str
            try:
                report = self.cc.probe()
                mode = billing_mode_from_probe(report.auth_mode)
                self._decide(
                    "l2_probe",
                    {"auth_mode": report.auth_mode, "billing_mode": mode},
                )
            except Exception as exc:  # ProbeError 等：判不准收紧（D2），不阻断开关动作
                mode = "api"
                self._decide("l2_probe", {"error": str(exc), "billing_mode": mode})
            cfg.billing_mode = mode
            save_agent_config(self.layout.agent_config_path, cfg)
        self.loop.budget.enabled = cfg.l2_enabled
        self.loop.budget.billing_mode = cfg.billing_mode or "api"
        return cfg

    # --- 崩溃恢复（启动期；running 残留 → abandoned；monitor 源按 R5 重放） ---
    def recover_orphans(self) -> list[TaskRecord]:
        """R5：全部残留先 abandoned（WAL 语义不变）；monitor 源再选择重放。

        防自旋=队列派生判据：同 dedup_key 任务数 ≥2（原任务+一次重放产物）即不再重放
        （重放任务若再崩成 abandoned，count=3 仍 >2 拦截——同键至多两个实体任务）；
        monitor_review 重放另要求当下 l2_enabled（关则记 task_replay_skipped 双保险）；
        重放≠保证执行——新任务消费期仍过全闸（L2/5 票/预算）。
        """
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            orphans = queue.replayables()
            for task in orphans:
                queue.transition(task.task_id, "abandoned")
                task.finished_at = _now_iso()
                task.error = "进程重启回收 running 残留（monitor 源按规则重放）"
                self._decide(
                    "task_abandoned",
                    {"task_id": task.task_id, "kind": task.kind, "code": task.code},
                )
            l2_on = load_agent_config(self.layout.agent_config_path).l2_enabled
            now = _dt.datetime.now(tz=_dt.UTC)
            for action in plan_replays(orphans, list(queue.tasks), l2_enabled=l2_on):
                if action.skip_reason is not None:
                    self._decide(
                        "task_replay_skipped",
                        {
                            "task_id": action.task_id,
                            "kind": action.kind,
                            "code": action.code,
                            "reason_code": action.skip_reason,
                        },
                    )
                    continue
                new = TaskRecord(
                    task_id=f"{action.dedup_key}:{now.strftime('%H%M%S')}r",
                    dedup_key=action.dedup_key,
                    kind=action.kind,
                    code=action.code,
                    enqueued_at=_now_iso(),
                    source="monitor",
                )
                queue.submit(new)  # abandoned 终态不阻 find_active
                self._decide(
                    "task_requeued",
                    {
                        "old_task_id": action.task_id,
                        "new_task_id": new.task_id,
                        "kind": action.kind,
                        "code": action.code,
                        "reason": "crash_recovery",
                    },
                )
            if orphans:
                atomic_write_json(self.layout.queue_path, queue.to_dict())
        return orphans

    # --- paper 确认流孤儿清理（W5：崩溃窗口脏态「已确认未走完链」启动自愈） ---
    def purge_confirmed_orphans(self) -> list[dict]:
        """清出 pending 中 state=confirmed 残留（正常流该状态是瞬态：confirm 后立即 remove）。

        语义：残留即崩溃证据（portfolio 落账与 pending 移除非原子窗口）；Operation 无
        request_id 锚、无法判定已落/未落，故不重放落账——逐笔决策留痕
        （paper_orphan_purged）供人工补账；sim.lock 内读-改-写（13-paper §⑤ 同纪律）。
        """
        from stock_trading_agent.paper import pending as paper_pending

        with FileLock(self.layout.sim_lock):
            queue = paper_pending.load(self.layout.sim_pending_fills_path)
            orphans = [
                {
                    "request_id": r.request_id,
                    "code": r.code,
                    "leg": r.tranche_leg,
                    "side": r.side,
                }
                for r in queue.requests
                if r.state == "confirmed"
            ]
            if not orphans:
                return []
            queue.requests = [r for r in queue.requests if r.state != "confirmed"]
            paper_pending.save(queue, self.layout.sim_pending_fills_path)
        for r in orphans:
            self._decide(
                "paper_orphan_purged",
                {**r, "note": "confirmed 残留清理（崩溃窗口）；如需落账请人工复核"},
            )
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
            # L2 消费闸（NR-4 收口）：monitor 源任务在 L2 关闭时一律拒起（无闸即拒起）。
            # 叠加序：defer（锁/窗，可恢复）在前，终态拒绝递进在后——防终态吞掉 defer 补跑。
            if (
                picked.source == "monitor"
                and picked.kind == MONITOR_REVIEW_KIND
                and not load_agent_config(self.layout.agent_config_path).l2_enabled
            ):
                queue.transition(picked.task_id, "failed")
                picked.finished_at = _now_iso()
                picked.error = "L2 总闸关闭：monitor 源 monitor_review 一律拒（NR-4 无闸即拒起）"
                self._decide(
                    "advice_refused",
                    {
                        "task_id": picked.task_id,
                        "kind": picked.kind,
                        "code": picked.code,
                        "origin": "gate",
                        "reason_code": "l2_disabled",
                        "reasons": [picked.error],
                    },
                )
                atomic_write_json(self.layout.queue_path, queue.to_dict())
                return picked
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
                    picked.session_uuid = request.session_uuid  # 观测反查键（0.0.13）
                    atomic_write_json(self.layout.queue_path, queue.to_dict())
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
                            **(
                                {"reason_code": "route_unknown"}
                                if exc.reason == RejectReason.UNKNOWN_KIND
                                else {}
                            ),  # §15 意图路由命中率的兜底计数（0.0.22）
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

    # --- briefing 自动触发（0.0.18 早晚分卡同批；L2 闸内、universe 全码、每股每槽每日一次） ---
    def _maybe_auto_briefing(self, now: _dt.datetime | None = None) -> list[TaskRecord]:
        """槽窗内为 universe 全码入队 briefing_morning/evening（判据纯函数见 briefing.py）。

        L2 总闸关闭不入队（无人值守自动拉起与 §⑩ 同源）；universe 不可读记
        briefing_auto_skipped 决策（宁拒不漏留痕）后跳过；幂等=find_any 含终态。
        """
        from stock_trading_agent.loop.briefing import plan_auto_briefings
        from stock_trading_agent.loop.events import MARKET_TZ
        from stock_trading_agent.loop.monitor_events import market_today

        local = (now or _dt.datetime.now(tz=_dt.UTC)).astimezone(MARKET_TZ)
        date = market_today(local)
        universe = self._universe_codes()
        if universe is None:
            self._decide(
                "briefing_auto_skipped",
                {"reason_code": "universe_unreadable", "date": date},
            )
            return []
        with FileLock(self.layout.queue_lock):
            queue = self.loop.load_queue()
            queued = {t.dedup_key for t in queue.tasks}
            planned = plan_auto_briefings(
                local, l2_enabled=self._l2_on(), universe_codes=universe, already_queued=queued
            )
            tasks = []
            for kind, code in planned:
                task = TaskRecord(
                    task_id=f"{kind}:{code}:{date}:auto",
                    dedup_key=f"{kind}:{code}:{date}",
                    kind=kind,
                    code=code,
                    enqueued_at=_now_iso(),
                    source="monitor",
                )
                queue.submit(task)
                tasks.append(task)
                self._decide(
                    "task_submitted",
                    {"task_id": task.task_id, "kind": kind, "code": code, "source": "monitor"},
                )
            if tasks:
                atomic_write_json(self.layout.queue_path, queue.to_dict())
        return tasks

    def _universe_codes(self) -> list[str] | None:
        """monitor/universe.json 全码（不分 real/sim；不可读→None）。"""
        import json

        path = self.layout.universe_path
        if not path.exists():
            return None
        try:
            rows = (json.loads(path.read_text(encoding="utf-8")).get("rows")) or []
            codes = sorted(
                {str(r.get("code")) for r in rows if isinstance(r, dict) and r.get("code")}
            )
            return codes or None
        except (json.JSONDecodeError, OSError, AttributeError):
            return None

    def _l2_on(self) -> bool:
        return load_agent_config(self.layout.agent_config_path).l2_enabled

    # --- 泄漏巡检（D-08，log-only） ---
    def scan_cc_leaks(self) -> list[tuple[int, str]]:
        """扫描在管外的 claude -p 孤儿进程；发现即决策 cc_orphan_detected（不 kill）。

        周期：启动一次 + worker 空转每小时一次（_last_leak_scan 节流）；DryRun 通道无进程跳过。
        """
        import time as _time

        live = getattr(self.cc, "live_pids", None)
        if live is None:
            return []  # DryRun 等无进程通道
        from stock_trading_agent.cc_client.leak import scan as leak_scan

        orphans = leak_scan(live(), runner=self._pgrep_runner)
        self._last_leak_scan = _time.monotonic()
        if orphans:
            self._decide(
                "cc_orphan_detected",
                {
                    "pids": [pid for pid, _ in orphans],
                    "action": "log",
                    "note": "D-08 泄漏巡检：log-only（kill 留 opt-in）",
                },
            )
        return orphans

    # --- worker 线程体 ---
    def _worker_loop(self) -> None:
        import time as _time

        while not self._stop.is_set():
            try:
                task = self.consume_one()
            except Exception:  # 消费异常不杀线程（守护线程死亡=编排停摆）
                traceback.print_exc()
                task = None
            if task is None:
                if _time.monotonic() - self._last_leak_scan > 3600:  # 每小时泄漏巡检
                    try:
                        self.scan_cc_leaks()
                    except Exception:
                        traceback.print_exc()
                if _time.monotonic() - self._last_auto_briefing_scan > 60:  # 槽窗检查（0.0.18）
                    self._last_auto_briefing_scan = _time.monotonic()
                    try:
                        self._maybe_auto_briefing()
                    except Exception:
                        traceback.print_exc()
                self._stop.wait(self.poll_interval_s)
