"""Loop 六段编排骨架（方案 §10）：ingest → route → run_session → assert_and_persist → feedback。

端口注入：CCClient / StateLayout / 预算闸（BudgetConfig）——dry-run 注入下端到端可跑。
断言违例 → task=failed + 决策记 refuse、不落产物、不归档转录（NR-3：归档在断言器放行后）；
触发源监听与后验反馈环 = stub（M1 接线期落地）。
"""

from __future__ import annotations

import datetime as _dt

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


class LoopReject(RuntimeError):
    """Loop 前置闸拒绝（禁跑窗 / 锁 / 幂等冲突 / 路由未命中 / 预算闸 / schema 缺位）。"""


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
            raise LoopReject(reason)
        if reason := lock_precheck(
            fetch_lock=self.layout.fetch_lock.exists(), watch_lock=self.layout.watch_lock.exists()
        ):
            raise LoopReject(reason)
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
            raise LoopReject(str(exc)) from exc
        return task

    # ②③ run_session：路由查表 → 预算外闸 → schema 注入 → CC 一次性会话（fresh context，§10③）
    def run_session(self, task: TaskRecord) -> tuple[CCRequest, object]:
        template = self.table.route(task.kind)
        if template is None:
            raise LoopReject(
                f"未知 kind 落兜底桶：{task.kind}（zero-LLM 分派与兜底策略属 M1 接线期）"
            )

        schema = schema_for_ref(template.schema_ref)
        if task.kind in OPINION_KINDS and schema is None:
            raise LoopReject(f"意见类 kind 缺 schema（§9 意见段必带，F-11/F-12/F-16）：{task.kind}")

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
        return request, self.cc.spawn_once(request)

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
            raise LoopReject(f"预算闸拒绝：{verdict.reason}")

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
        queue.transition(task.task_id, "running")
        if not verdict.ok:
            queue.transition(task.task_id, "failed")
            self.decisions.append(
                _now_iso(),
                kind="assert_refuse",
                payload={"task_id": task.task_id, "reasons": list(verdict.reasons)},
            )
            self._persist_all(queue, sessions)
            return False
        queue.transition(task.task_id, "done")
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
