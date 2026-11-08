"""Loop 六段编排（方案 §10）：ingest → route → run_session → assert_and_persist → feedback。

端口注入：CCClient / StateLayout / 预算闸（BudgetConfig）——dry-run 注入下端到端可跑。
断言违例 → task=failed + 决策记 refuse、不落产物、不归档转录（NR-3：归档在断言器放行后）。
运行时接线（worker/watcher/T1）见 loop/worker.py；后验反馈环消费侧在外仓 postmortem 槽（M2）。
"""

from __future__ import annotations

import datetime as _dt
from enum import StrEnum

from stock_trading_agent.budget.gate import BudgetConfig, check, record_dual_axis
from stock_trading_agent.cc_client.contract import (
    CCClient,
    CCRequest,
    derive_session_uuid,
    validate_request,
)
from stock_trading_agent.loop.assertor import OPINION_KINDS, assess_result
from stock_trading_agent.loop.events import (
    EventEnvelope,
    dedup_key,
    gate_report_event,
    lock_precheck,
)
from stock_trading_agent.loop.router import RoutingTable
from stock_trading_agent.loop.schemas import schema_for_ref
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import QueueError, QueueState, TaskRecord
from stock_trading_agent.state.sessions import SessionRecord, SessionsState
from stock_trading_agent.state.store import atomic_write_json, read_json


class RejectReason(StrEnum):
    """Loop 前置闸拒绝原因码（面板 HTTP 映射消费：409/423/422，12-orchestration §⑥）。"""

    WINDOW = "window"  # 17:30–18:10 禁跑窗
    LOCK = "lock"  # fetch_state.lock / watch.lock 在场
    DUPLICATE = "duplicate"  # 幂等键冲突
    UNKNOWN_KIND = "unknown_kind"  # 路由未命中
    SCHEMA = "schema"  # 意见类 kind 缺 schema
    BUDGET = "budget"  # 预算外闸拒绝
    INTERNAL = "internal"  # 内部不变式违例（如 task 未处 running）


class LoopReject(RuntimeError):
    """Loop 前置闸拒绝（禁跑窗 / 锁 / 幂等冲突 / 路由未命中 / 预算闸 / schema 缺位）。

    reason：拒绝原因码（RejectReason）；缺省 INTERNAL（向后兼容既有 str 语义）。
    """

    def __init__(self, message: str, reason: RejectReason = RejectReason.INTERNAL) -> None:
        super().__init__(message)
        self.reason = reason


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds")


class AgentLoop:
    """单次执行路径的六段编排（骨架：无后台线程、无 watchfiles 监听）。"""

    def __init__(
        self,
        *,
        cc: CCClient,
        layout: StateLayout,
        table: RoutingTable,
        decisions: DecisionLog | None = None,
        budget: BudgetConfig | None = None,
    ) -> None:
        self.cc = cc
        self.layout = layout
        self.table = table
        self.decisions = decisions or DecisionLog()
        self.budget = budget or BudgetConfig()  # 默认关（人在环期仅记账，§13）

    # ① ingest：事件信封入队（幂等键 + 禁跑窗 + 锁前置）
    def ingest(self, event: EventEnvelope, queue: QueueState) -> TaskRecord:
        now = _dt.datetime.now(tz=_dt.UTC)
        if reason := gate_report_event(now, event.kind):
            raise LoopReject(reason, RejectReason.WINDOW)
        if reason := lock_precheck(
            fetch_lock=self.layout.fetch_lock.exists(), watch_lock=self.layout.watch_lock.exists()
        ):
            raise LoopReject(reason, RejectReason.LOCK)
        key = dedup_key(event, date=now.strftime("%Y-%m-%d"))
        task = TaskRecord(
            task_id=f"{key}:{now.strftime('%H%M%S')}",
            dedup_key=key,
            kind=event.kind,
            code=event.code,
            enqueued_at=_now_iso(),
        )
        try:
            queue.submit(task)
        except QueueError as exc:
            raise LoopReject(str(exc), RejectReason.DUPLICATE) from exc
        return task

    # ②③ run_session：路由查表 → 预算外闸 → schema 注入 → running 落盘 → CC 一次性会话
    # （fresh context，§10③；queue 必填——spawn 前 mark_running 落盘是 WAL 不变式，漏传即 TypeError）
    def run_session(self, task: TaskRecord, *, queue: QueueState) -> tuple[CCRequest, object]:
        request = self.build_request(task)
        self.mark_running(task, queue, session_uuid=request.session_uuid)
        return request, self.cc.spawn_once(request)

    def build_request(self, task: TaskRecord) -> CCRequest:
        """②③ 前半：路由查表 → 预算外闸 → schema 注入 → CCRequest 组装与校验（不 spawn 不迁移）。

        worker 两段式锁消费：queue.lock 内 build+mark_running 落盘，锁外 spawn。
        """
        template = self.table.route(task.kind)
        if template is None:
            raise LoopReject(
                f"未知 kind 落兜底桶：{task.kind}（面板提交白名单在 server 层拦；兜底即拒）",
                RejectReason.UNKNOWN_KIND,
            )

        schema = schema_for_ref(template.schema_ref)
        if task.kind in OPINION_KINDS and schema is None:
            raise LoopReject(
                f"意见类 kind 缺 schema（§9 意见段必带，F-11/F-12/F-16）：{task.kind}",
                RejectReason.SCHEMA,
            )

        budget_usd = template.budget_usd
        if self.budget.billing_mode == "subscription":
            # D2①：订阅形态 --max-budget-usd 带高位哨兵（NR-4 旗标在场性；数值闸在 gate.check）
            sentinel = float(self.table.defaults.get("sentinel_budget_usd", 0.0))
            budget_usd = max(template.budget_usd, sentinel)

        self._budget_precheck(session_est_usd=template.budget_usd)

        date = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")
        request = CCRequest(
            task_id=task.task_id,
            session_uuid=derive_session_uuid(date, task.code, task.kind),
            prompt=template.prompt_template.format(code=task.code),
            schema=schema,  # 每 kind 注入对应 draft-07 schema（喂 CC 与本地复验同一份）
            budget_usd=budget_usd,
            max_turns=template.max_turns,
            allowed_tools=template.allowed_tools,
        )
        validate_request(request)
        return request

    def mark_running(
        self, task: TaskRecord, queue: QueueState, *, session_uuid: str | None = None
    ) -> TaskRecord:
        """spawn/子进程启动**前**迁移 running 并落盘（WAL 不变式，12-orchestration §③）。

        崩溃时盘上必见 running 残留 → 启动期 abandoned 回收；spawn 后不迁移（收严）。
        """
        queue.transition(task.task_id, "running")
        task.started_at = _now_iso()
        task.session_uuid = session_uuid
        atomic_write_json(self.layout.queue_path, queue.to_dict())
        return task

    def _budget_precheck(self, *, session_est_usd: float) -> None:
        """§13 外闸（发送前预检 + 硬次数闸；enabled=False 全放行——骨架默认关）。"""
        ledger = read_json(self.layout.budget_path) or {}
        verdict = check(
            self.budget,
            session_est_usd=session_est_usd,
            day_spent_usd=float(ledger.get("cost_usd_equiv", 0.0)),
            month_spent_usd=float(ledger.get("month_cost_usd_equiv", 0.0)),
            day_task_count=int(ledger.get("tasks", 0)),
        )
        if not verdict.allowed:
            raise LoopReject(f"预算闸拒绝：{verdict.reason}", RejectReason.BUDGET)

    # ④⑤ assert_and_persist：断言器过门 → queue 迁移 + decisions 追加 + sessions 登记 + 转录归档
    def assert_and_persist(
        self,
        task: TaskRecord,
        request: CCRequest,
        result,
        *,
        queue: QueueState,
        sessions: SessionsState,
    ) -> bool:
        verdict = assess_result(result, kind=task.kind, schema=request.schema)
        if task.status != "running":  # 守卫：spawn 前未 mark_running 即拒（收严，不变式不可绕过）
            raise LoopReject(
                f"内部不变式违例：task={task.task_id} 在 assert 时处 {task.status!r} 而非 running"
                "（run_session 须先 mark_running 落盘）",
                RejectReason.INTERNAL,
            )
        if not verdict.ok:
            queue.transition(task.task_id, "failed")
            record = next(t for t in queue.tasks if t.task_id == task.task_id)
            record.finished_at = _now_iso()
            record.error = "；".join(verdict.reasons)
            self.decisions.append(
                _now_iso(),
                kind="assert_refuse",
                payload={"task_id": task.task_id, "reasons": list(verdict.reasons)},
            )
            self._persist_all(queue, sessions)
            return False
        queue.transition(task.task_id, "done")
        record = next(t for t in queue.tasks if t.task_id == task.task_id)
        record.finished_at = _now_iso()
        self.cc.archive(request)  # NR-3 次序：转录归档在断言器放行之后
        sessions.upsert(
            SessionRecord(
                session_uuid=request.session_uuid,
                task_id=task.task_id,
                transcript_path=str(result.transcript_path),
                usage_tokens=dict(result.usage_tokens),
                cost_usd_equiv=float((result.envelope or {}).get("total_cost_usd", 0.0)),
                ts=_now_iso(),
            )
        )
        self._record_budget(result)
        self.decisions.append(
            _now_iso(),
            kind="task_done",
            payload={"task_id": task.task_id, "kind": task.kind, "code": task.code},
        )
        self._persist_all(queue, sessions)
        return True

    def _record_budget(self, result) -> None:
        """订阅轨双口径记账（F-34：token + 等效美元；原子写 budget.json，§11 写者=主 Agent）。"""
        ledger = read_json(self.layout.budget_path) or {}
        updated = record_dual_axis(
            dict(result.usage_tokens),
            float((result.envelope or {}).get("total_cost_usd", 0.0)),
            ledger,
        )
        atomic_write_json(self.layout.budget_path, updated)

    def _persist_all(self, queue: QueueState, sessions: SessionsState) -> None:
        atomic_write_json(self.layout.queue_path, queue.to_dict())
        atomic_write_json(self.layout.sessions_path, sessions.to_dict())
        if self.layout.decisions_path.parent.exists():
            from dataclasses import asdict

            from stock_trading_agent.state.store import atomic_append_jsonl

            if self.decisions.lines:
                atomic_append_jsonl(self.layout.decisions_path, asdict(self.decisions.lines[-1]))

    def load_queue(self) -> QueueState:
        return QueueState.from_dict(read_json(self.layout.queue_path, expect_version=1))

    def load_sessions(self) -> SessionsState:
        return SessionsState.from_dict(read_json(self.layout.sessions_path, expect_version=1))
