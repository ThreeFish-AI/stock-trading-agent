"""Loop 六段编排（方案 §10）：ingest → route → run_session → assert_and_persist → feedback。

端口注入：CCClient / StateLayout / 预算闸（BudgetConfig）——dry-run 注入下端到端可跑。
断言违例 → task=failed + 决策记 refuse、不落产物、不归档转录（NR-3：归档在断言器放行后）。
运行时接线（worker/watcher/T1）见 loop/worker.py；后验反馈环消费侧在外仓 postmortem 槽（M2）。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from enum import StrEnum
from pathlib import Path

from stock_trading_agent.budget.gate import BudgetConfig, check, record_dual_axis
from stock_trading_agent.cc_client.contract import (
    CCClient,
    CCRequest,
    derive_session_uuid,
    validate_request,
)
from stock_trading_agent.loop.advice import (
    MONITOR_REVIEW_KIND,
    advice_context,
    render_advice_card,
    scan_advice_text,
    write_advice_card,
)
from stock_trading_agent.loop.assertor import OPINION_KINDS, assess_result
from stock_trading_agent.loop.briefing import (
    BRIEFING_KIND,
    build_card_md,
    persist_card,
    scan_briefing_text,
)
from stock_trading_agent.loop.events import (
    EventEnvelope,
    dedup_key,
    gate_report_event,
    lock_is_held,
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
        self._persisted_lines = 0  # _persist_all 增量落行游标（advice 链单次 persist 多条决策）
        self.budget = budget or BudgetConfig()  # 默认关（人在环期仅记账，§13）

    # ① ingest：事件信封入队（幂等键 + 禁跑窗 + 锁前置）
    def ingest(self, event: EventEnvelope, queue: QueueState) -> TaskRecord:
        now = _dt.datetime.now(tz=_dt.UTC)
        if reason := gate_report_event(now, event.kind):
            raise LoopReject(reason, RejectReason.WINDOW)
        if reason := lock_precheck(
            fetch_lock=lock_is_held(self.layout.fetch_lock),
            watch_lock=lock_is_held(self.layout.watch_lock),
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

    def _session_dir(self, session_uuid: str) -> Path:
        """CC 会话 cwd（agent/runs/<session_uuid>/；spawn 前建，12-orchestration §④）。"""
        session_dir = self.layout.runs_dir / session_uuid
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

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
        request = dataclasses.replace(request, cwd=str(self._session_dir(request.session_uuid)))
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
        """§13 外闸（发送前预检 + 硬次数闸；enabled=False 全放行——默认关）。

        0.0.9 起读日/月分桶（market_today 钟面，与 count_t2_done 同口径）——修月闸空转
        与 tasks 全期计数两个实 bug（12-orchestration §⑩）。
        """
        from stock_trading_agent.loop.monitor_events import market_today

        ledger = read_json(self.layout.budget_path) or {}
        today = market_today()
        day_bucket = (ledger.get("daily") or {}).get(today) or {}
        month_bucket = (ledger.get("monthly") or {}).get(today[:7]) or {}
        verdict = check(
            self.budget,
            session_est_usd=session_est_usd,
            day_spent_usd=float(day_bucket.get("usd", 0.0)),
            month_spent_usd=float(month_bucket.get("usd", 0.0)),
            day_task_count=int(day_bucket.get("tasks", 0)),
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
        ctx = advice_context(self.layout, task.code) if task.kind == MONITOR_REVIEW_KIND else None
        verdict = assess_result(result, kind=task.kind, schema=request.schema, advice_ctx=ctx)
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
                kind=("advice_refused" if task.kind == MONITOR_REVIEW_KIND else "assert_refuse"),
                payload={
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "code": task.code,
                    **(
                        {"origin": "assert", "reason_code": "schema_or_redline"}
                        if task.kind == MONITOR_REVIEW_KIND
                        else {}
                    ),
                    "reasons": list(verdict.reasons),
                },
            )
            self._persist_all(queue, sessions)
            return False
        if task.kind == MONITOR_REVIEW_KIND:
            declined = self._advice_card_or_refuse(task, result, queue, sessions, ctx)
            if declined is not None:
                return declined  # 写卡链失败 → task=failed + advice_refused，杜绝 done-无卡
        if task.kind == BRIEFING_KIND:
            declined = self._briefing_card_or_fail(task, result, queue, sessions)
            if declined is not None:
                return declined
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
            payload={
                "task_id": task.task_id,
                "kind": task.kind,
                "code": task.code,
                "permission_denials": getattr(result, "permission_denials", 0),  # F-28 审计消费
            },
        )
        self._persist_all(queue, sessions)
        return True

    def _advice_card_or_refuse(
        self, task: TaskRecord, result, queue: QueueState, sessions: SessionsState, ctx
    ) -> bool | None:
        """T2 建议卡写者例外执行序（NR-1/NR-3；07 P2 契约路径）。

        outcome=card：render → 脱敏扫描 → 原子写 → advice_card_written 决策（任何一步失败即
        failed + advice_refused，杜绝 done-无卡）；outcome=facts_only：会话自报降级——task 照常
        done（会话/预算照登），另记 advice_refused(origin=session_degraded) 携 facts_note。
        """
        structured = result.structured_output or {}
        if structured.get("outcome") != "card":
            self.decisions.append(
                _now_iso(),
                kind="advice_refused",
                payload={
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "code": task.code,
                    "origin": "session_degraded",
                    "reason_code": "session_degraded",
                    "facts_note": str(structured.get("facts_note", "")),
                },
            )
            return None  # 正常降级：继续 done 路径
        origin = None
        card_path = None
        try:
            md = render_advice_card(
                structured, code=task.code, date=ctx.today, task_id=task.task_id
            )
            if errs := scan_advice_text(md):
                origin, reason_code, detail = "render_scan", "render_scan", "；".join(errs)
            else:
                card_path = write_advice_card(self.layout, ctx.today, task.code, md)
        except OSError as exc:
            origin, reason_code, detail = "write_failure", "write_failure", str(exc)
        if origin is not None:
            queue.transition(task.task_id, "failed")
            record = next(t for t in queue.tasks if t.task_id == task.task_id)
            record.finished_at = _now_iso()
            record.error = detail
            self.decisions.append(
                _now_iso(),
                kind="advice_refused",
                payload={
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "code": task.code,
                    "origin": origin,
                    "reason_code": reason_code,
                    "reasons": [detail],
                },
            )
            self._persist_all(queue, sessions)
            return False
        self.decisions.append(
            _now_iso(),
            kind="advice_card_written",
            payload={
                "task_id": task.task_id,
                "code": task.code,
                "date": ctx.today,
                "path": str(card_path),
                "candidate_action": structured.get("candidate_action"),
                "final_signal": structured.get("final_signal"),
            },
        )
        return None

    def _briefing_card_or_fail(
        self, task: TaskRecord, result, queue: QueueState, sessions: SessionsState
    ) -> bool | None:
        """晨报/复盘卡落盘钩子（脱敏三层之卡面负断言；失败即 failed 杜绝 done-无卡）。"""
        from stock_trading_agent.loop.monitor_events import market_today

        structured = result.structured_output or {}
        date = market_today()
        try:
            md = build_card_md(structured, date=date, code=task.code)
            problems = scan_briefing_text(md)
            if problems:
                raise ValueError("；".join(problems))
            card_path = persist_card(self.layout, date, task.code, md)
        except (OSError, ValueError) as exc:
            queue.transition(task.task_id, "failed")
            record = next(t for t in queue.tasks if t.task_id == task.task_id)
            record.finished_at = _now_iso()
            record.error = str(exc)
            self.decisions.append(
                _now_iso(),
                kind="task_failed",
                payload={
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "code": task.code,
                    "reasons": [str(exc)],
                },
            )
            self._persist_all(queue, sessions)
            return False
        self.decisions.append(
            _now_iso(),
            kind="briefing_card",
            payload={
                "task_id": task.task_id,
                "code": task.code,
                "date": date,
                "path": str(card_path),
            },
        )
        return None

    def _record_budget(self, result) -> None:
        """双口径记账（F-34：token+等效美元+日/月分桶；原子写，§11 写者=主 Agent）。"""
        from stock_trading_agent.loop.monitor_events import market_today

        ledger = read_json(self.layout.budget_path) or {}
        day = market_today()
        updated = record_dual_axis(
            dict(result.usage_tokens),
            float((result.envelope or {}).get("total_cost_usd", 0.0)),
            ledger,
            day=day,
            month=day[:7],
        )
        atomic_write_json(self.layout.budget_path, updated)

    def _persist_all(self, queue: QueueState, sessions: SessionsState) -> None:
        atomic_write_json(self.layout.queue_path, queue.to_dict())
        atomic_write_json(self.layout.sessions_path, sessions.to_dict())
        if self.layout.decisions_path.parent.exists():
            from dataclasses import asdict

            from stock_trading_agent.state.store import atomic_append_jsonl

            fresh = self.decisions.lines[self._persisted_lines :]
            for line in fresh:
                atomic_append_jsonl(self.layout.decisions_path, asdict(line))
            self._persisted_lines = len(self.decisions.lines)

    def load_queue(self) -> QueueState:
        return QueueState.from_dict(read_json(self.layout.queue_path, expect_version=1))

    def load_sessions(self) -> SessionsState:
        return SessionsState.from_dict(read_json(self.layout.sessions_path, expect_version=1))
