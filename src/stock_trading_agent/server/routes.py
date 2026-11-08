"""面板路由：只读态 + 提交半边 + monitor/reports 只读投影 + HTML fragment（§11/§16）。

写动词 POST /api/queue/submit 与 /api/panel/submit 经进程内 LoopRuntime.submit（写者=主
Agent，§11「读+提交命令」）；全部端点 def（阻塞 IO 走线程池，I-38）。
NR-5：monitor events/universe 本身即脱敏产物（公开行情+规则事实），透出合规；
postmortem 槽探测只读 ledger.json mtime（本层可引用，state/ 层禁——红线扫描机器门）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ValidationError

from stock_trading_agent import __version__
from stock_trading_agent.loop.advice import parse_advice_card
from stock_trading_agent.loop.events import lock_is_held
from stock_trading_agent.loop.loop import LoopReject
from stock_trading_agent.loop.monitor_events import market_today, parse_line
from stock_trading_agent.server.panel import (
    render_audit,
    render_events_table,
    render_submit_result,
    render_task_card,
)
from stock_trading_agent.server.schemas import REJECT_STATUS, SubmitRequest
from stock_trading_agent.state.paths import StateLayout

router = APIRouter(prefix="/api")
root_router = APIRouter()  # 无前缀（favicon 等根级路由）

_REPORTS_DIR_RE = re.compile(r"^\d{5,6}\.(cn|hk)$")


def _layout(request: Request) -> StateLayout:
    root = getattr(request.app.state, "state_root", None)
    if root is not None:
        return StateLayout(root=Path(root))
    return StateLayout.from_env()


def _runtime(request: Request):
    return getattr(request.app.state, "loop_runtime", None)


# --- 只读态（§11 面板读半边） ---


@root_router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    from stock_trading_agent.server.app import _STATIC_DIR

    return FileResponse(_STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@router.get("/health")
def health() -> dict:
    return {"ok": True, "component": "stock-trading-agent-panel", "version": __version__}


@router.get("/queue")
def queue(request: Request) -> dict:
    from stock_trading_agent.state.queue import QueueState
    from stock_trading_agent.state.store import read_json_cached

    data = read_json_cached(_layout(request).queue_path)
    return data if data is not None else QueueState().to_dict()


@router.get("/sessions")
def sessions(request: Request) -> dict:
    from stock_trading_agent.state.sessions import SessionsState
    from stock_trading_agent.state.store import read_json_cached

    data = read_json_cached(_layout(request).sessions_path)
    return data if data is not None else SessionsState().to_dict()


@router.get("/decisions")
def decisions(request: Request, tail: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    from stock_trading_agent.state.store import read_jsonl_lines_cached

    lines = [json.loads(raw) for raw in read_jsonl_lines_cached(_layout(request).decisions_path)]
    return lines[-tail:]


@router.get("/paper")
def paper(request: Request) -> dict:
    from stock_trading_agent.paper import ledger

    layout = _layout(request)
    portfolio = ledger.load(layout.sim_portfolio_path)
    if portfolio is None:
        return {"exists": False, "positions": [], "operations": []}
    return {"exists": True, **portfolio.model_dump()}


@router.get("/budget")
def budget(request: Request) -> dict:
    from stock_trading_agent.state.store import read_json_cached

    data = read_json_cached(_layout(request).budget_path)
    return (
        data
        if data is not None
        else {
            "enabled": False,
            "billing_mode": "subscription",
            "note": "预算台账未初始化（默认关，仅记账）",
        }
    )


# --- vibe-stock 只读投影（三槽状态条/事件流/报告索引；零 LLM） ---


@router.get("/monitor/events")
def monitor_events(
    request: Request,
    date: str | None = None,
    tail: int = Query(default=50, ge=1, le=500),
) -> dict:
    return _monitor_events_payload(request, date=date, tail=tail)


def _monitor_events_payload(request: Request, *, date: str | None, tail: int) -> dict:
    """核心载荷（HTTP 端点与 fragment 复用；tail 夹紧 1..500；R2 缓存读）。"""
    from stock_trading_agent.state.store import read_jsonl_lines_cached

    tail = max(1, min(int(tail), 500))
    day = date or market_today()
    path = _layout(request).monitor_events_file(day)
    events: list[dict] = []
    skipped = 0
    for raw in read_jsonl_lines_cached(path):
        if not raw.strip():
            continue
        ev = parse_line(raw)
        if ev is None:
            skipped += 1
            continue
        events.append(
            {
                "ts": ev.ts,
                "code": ev.code,
                "market": ev.market,
                "kind": ev.kind,
                "rule": ev.rule,
                "level": ev.level,
                "notify": ev.notify,
                "mech_risk": ev.mech_risk,
                "facts": ev.facts,  # 0.0.11 透出：现价/距线等机械事实（①③两页呈现源）
                "review": ev.review,
                "suppress_reason": ev.suppress_reason,
            }
        )
    return {"date": day, "count": len(events), "skipped": skipped, "events": events[-tail:]}


@router.get("/monitor/status")
def monitor_status(request: Request) -> dict:
    from stock_trading_agent.state.store import read_json_cached

    layout = _layout(request)
    day = market_today()
    watch_state = read_json_cached(layout.monitor_state_path) or {}
    runtime = _runtime(request)

    queued = running = 0
    q = read_json_cached(layout.queue_path) or {"tasks": []}
    for t in q.get("tasks", []):
        if t.get("status") == "queued":
            queued += 1
        elif t.get("status") == "running":
            running += 1

    # postmortem 槽完成探测：ledger.json（绩效因子台账）mtime 日期==今日（只读；state/ 层禁引此名）
    pm_done = False
    ledger_path = layout.root / "ledger.json"
    try:
        pm_done = ledger_path.exists() and (_mtime_day(ledger_path) == day)
    except OSError:
        pm_done = False

    return {
        "date": day,
        "locks": {
            # 持有态探测（0.0.5 收敛）：存在性会把 vibe-stock 不删除的陈旧锁文件误判为持有中
            "watch": lock_is_held(layout.watch_lock),
            "fetch": lock_is_held(layout.fetch_lock),
        },
        "watch_state": {
            "close_done": bool(watch_state.get("close_done")),
            "next_due": watch_state.get("next_due") or {},
            "src_fail": watch_state.get("src_fail") or {},
        },
        "slots": {
            "monitor": {"done": bool(watch_state.get("close_done"))},
            "outlook": {"done": layout.outlook_file(day).exists(), "file": f"outlook/{day}.md"},
            "postmortem": {"done": pm_done},
        },
        "loop": {
            "active": bool(runtime and runtime.is_active),
            "queued": queued,
            "running": running,
        },
    }


def _mtime_day(path: Path) -> str:
    import datetime as dt

    return (
        dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
        .astimezone(dt.timezone(dt.timedelta(hours=8)))
        .strftime("%Y-%m-%d")
    )


@router.get("/reports")
def reports(request: Request) -> dict:
    layout = _layout(request)
    out: list[dict] = []
    root = layout.reports_dir
    if root.exists():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not _REPORTS_DIR_RE.match(d.name):
                continue
            code, _, market = d.name.partition(".")
            files = sorted(p.name for p in d.glob("*.md"))
            if not files:
                continue
            out.append(
                {
                    "code": code,
                    "market": market,
                    "dir": d.name,
                    "latest": files[-1],
                    "count": len(files),
                    "files": files[-10:],
                }
            )
            if len(out) >= 50:
                break
    out.sort(key=lambda r: r["latest"], reverse=True)
    return {"reports": out}


# --- P2 端点（0.0.12：names/calendar/notifications，全 GET 只读） ---


@router.get("/calendar")
def calendar(request: Request) -> dict:
    """事件日历：当日 T1 workdir + 最近全诊会话 workdir 的 evidence 三块聚合（零新拉数据）。"""
    from stock_trading_agent.server.panel_data import aggregate_calendar, load_root_json

    layout = _layout(request)
    from stock_trading_agent.loop.monitor_events import market_today

    payloads: list[dict] = []
    t1_root = layout.runs_dir / market_today() / "t1"
    if t1_root.exists():
        for d in sorted(t1_root.iterdir()):
            ev = load_root_json(d / "evidence.json")
            if isinstance(ev, dict):
                payloads.append(ev)
    # 最近会话 workdir（sessions 反查，按 code 去重取最新——aggregate_calendar 内 seen 去重）
    for sess in list((sessions(request).get("sessions") or {}).values())[-30:]:
        uuid = sess.get("session_uuid") if isinstance(sess, dict) else None
        if not uuid:
            continue
        ev = load_root_json(layout.runs_dir / str(uuid) / ".vs" / "evidence.json")
        if isinstance(ev, dict):
            payloads.append(ev)
    return {"upcoming": aggregate_calendar(payloads)}


@router.get("/notifications")
def notifications(request: Request, tail: int = 20) -> list[dict]:
    """已发通知历史重放（只读；产品仓永不发通知 R7）。"""
    from stock_trading_agent.server.panel_data import (
        load_name_map,
        rebuild_notifications,
    )

    layout = _layout(request)
    events = _monitor_events_payload(request, date=None, tail=500).get("events", [])
    from stock_trading_agent.state.store import read_text_cached

    log_text = read_text_cached(layout.monitor_dir / "logs" / "watch.out.log")
    names = load_name_map(layout)
    rows = rebuild_notifications(events, log_text, names)
    return rows[-tail:]


# --- 面板新端点（0.0.11：signals/todos/topbar/phase/报告与建议卡 fragment，全 GET） ---


@router.get("/signals")
def signals(request: Request) -> dict:
    """在册信号快照（signals.json 只读 + sim last_price/events facts 现价组合）。"""
    from stock_trading_agent.server.panel_data import (
        build_name_map,
        load_root_json,
        signals_snapshot,
    )

    layout = _layout(request)
    entries = load_root_json(layout.root / "signals.json")
    price_map: dict[str, float | None] = {}
    from stock_trading_agent.paper import ledger as paper_ledger

    portfolio = paper_ledger.load(layout.sim_portfolio_path)
    if portfolio:
        for pos in portfolio.positions:
            price_map[pos.code] = pos.last_price
    for ev in _monitor_events_payload(request, date=None, tail=500).get("events", []):
        facts = ev.get("facts") or {}
        price = facts.get("price")
        if price is not None and ev.get("code"):
            price_map.setdefault(str(ev["code"]), float(price))
    snap = signals_snapshot(entries if isinstance(entries, list) else None, price_map)
    from stock_trading_agent.server.panel_data import load_name_map

    snap["name_map"] = build_name_map(reports(request).get("reports", []))
    snap["names"] = load_name_map(layout, signals=entries)  # R4：signals 单请求单读（原先 2 次）
    return snap


@router.get("/todos")
def todos(request: Request) -> dict:
    """今日待办三源聚合（pending fills → mech_risk 事件 → briefing 未 ack）。"""
    from stock_trading_agent.server.panel_data import derive_todos

    layout = _layout(request)
    events = _monitor_events_payload(request, date=None, tail=500).get("events", [])
    from stock_trading_agent.paper import pending as paper_pending

    pending = [
        vars(r) | {"bar_date": r.bar.date}
        for r in paper_pending.load(layout.sim_pending_fills_path).requests
    ]
    brief = _briefing_payload(request).get("cards", [])
    return {"todos": derive_todos(events, pending, brief)}


@router.get("/panel/phase")
def panel_phase(request: Request) -> HTMLResponse:
    """顶栏右侧：日期周几 + 盘态 pill。"""
    from stock_trading_agent.server.panel import render_phase_chip
    from stock_trading_agent.server.panel_data import market_phase

    return HTMLResponse(render_phase_chip(market_phase()))


@router.get("/panel/topbar")
def panel_topbar(request: Request) -> HTMLResponse:
    """①页 hero 右：刷新时刻 + 盘态。"""
    import datetime as dt

    from stock_trading_agent.server.panel import render_phase_chip
    from stock_trading_agent.server.panel_data import market_phase

    now = dt.datetime.now(tz=dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=8)))
    chip = render_phase_chip(market_phase())
    return HTMLResponse(f'<span class="metachip">刷新 {now:%H:%M:%S} · 180s 轮询</span> {chip}')


@router.get("/panel/signals")
def panel_signals(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_signals_table

    return HTMLResponse(render_signals_table(signals(request)))


@router.get("/session/{task_id}")
def session_view(task_id: str, request: Request) -> dict:
    """会话中间产物视图（queue 反查 session_uuid → .vs 四件解析；P2 §⑫ 案 a）。"""
    from stock_trading_agent.server.session_products import (
        opinions_board,
        read_session_products,
        stage_status,
        verdict_card,
    )

    layout = _layout(request)
    task = next(
        (t for t in (queue(request).get("tasks") or []) if t.get("task_id") == task_id), None
    )
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    uuid = task.get("session_uuid") or ""
    if not uuid:
        raise HTTPException(
            status_code=404, detail="任务无 session_uuid（未 spawn 或 dry-run 轨迹）"
        )
    view = read_session_products(layout.runs_dir, uuid, layout.reports_dir)
    return {
        "task_id": task_id,
        "session_uuid": uuid,
        "stages": stage_status(view),
        "opinions": opinions_board(view),
        "verdict": verdict_card(view),
        "report_path": str(view.report_path) if view.report_path else None,
        "notes": view.notes,
    }


@router.get("/panel/session/{task_id}")
def panel_session(task_id: str, request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_session_obs

    return HTMLResponse(render_session_obs(session_view(task_id, request)))


@router.get("/panel/calendar")
def panel_calendar(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_calendar

    return HTMLResponse(render_calendar(calendar(request)))


@router.get("/panel/notifications")
def panel_notifications(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_notifications

    return HTMLResponse(render_notifications({"items": notifications(request, tail=10)}))


@router.get("/panel/todos")
def panel_todos(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_todos

    return HTMLResponse(render_todos(todos(request)))


@router.get("/panel/navcard")
def panel_navcard(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_navcard

    return HTMLResponse(render_navcard(_paper_view(request)))


@router.get("/panel/syscard")
def panel_syscard(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_syscard

    return HTMLResponse(render_syscard(monitor_status(request), sessions(request), queue(request)))


@router.get("/panel/overview")
def panel_overview(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_event_feed

    payload = _monitor_events_payload(request, date=None, tail=50)
    return HTMLResponse(render_event_feed(payload))


@router.get("/panel/mometa")
def panel_mometa(request: Request) -> HTMLResponse:
    """③页 hero 右：watch 心跳 + 零 LLM 标识。"""
    status = monitor_status(request)
    watch_state = status.get("watch_state") or {}
    next_due = (watch_state.get("next_due") or {}).get("cn") or ""
    beat = next_due[:16] if next_due else "—"
    return HTMLResponse(
        f'<span class="metachip">watch 心跳 {beat}</span>'
        '<span class="metachip">T0 机械判据族 · 零 LLM</span>'
    )


@router.get("/panel/dgmeta")
def panel_dgmeta(request: Request) -> HTMLResponse:
    """②页 hero 右：会话数 + 预算双口径。"""
    sess = sessions(request)
    budget_data = budget(request)
    return HTMLResponse(
        f'<span class="metachip">会话 {len(sess.get("sessions", {}))}</span>'
        f'<span class="metachip">预算 {budget_data.get("cost_usd_equiv", 0)} 等效 USD</span>'
    )


@router.get("/panel/aumeta")
def panel_aumeta(request: Request) -> HTMLResponse:
    lines = decisions(request, tail=1)
    head = (lines[-1].get("hash", "")[:12] + "…") if lines else "空链"
    return HTMLResponse(f'<span class="metachip">hash 链 head {head}</span>')


@router.get("/panel/dgform")
def panel_dgform(request: Request) -> HTMLResponse:
    """②页发起诊股表单（六 kind 全量下拉 + 持仓成本说明位）。"""
    from stock_trading_agent.server.panel import render_diag_form

    return HTMLResponse(render_diag_form())


@router.get("/panel/tasks")
def panel_tasks(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_tasks_table

    return HTMLResponse(render_tasks_table(queue(request)))


@router.get("/panel/reports")
def panel_reports(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_reports_list

    return HTMLResponse(render_reports_list(reports(request)))


@router.get("/panel/report/{code_market}/{filename}")
def panel_report_body(code_market: str, filename: str, request: Request) -> HTMLResponse:
    """②报告正文 fragment（三保险路径安全：目录正则+日期文件名正则+resolve 边界）。"""
    import re as _re

    from stock_trading_agent.server.panel import render_report_body

    layout = _layout(request)
    if not _REPORTS_DIR_RE.match(code_market) or not _re.fullmatch(
        r"\d{4}-\d{2}-\d{2}\.md", filename
    ):
        raise HTTPException(status_code=404, detail="报告路径非法")
    path = (layout.reports_dir / code_market / filename).resolve()
    if not path.is_relative_to(layout.reports_dir.resolve()) or not path.exists():
        raise HTTPException(status_code=404, detail="报告不存在")
    return HTMLResponse(render_report_body(code_market, filename, path.read_text(encoding="utf-8")))


@router.get("/panel/advice-detail/{date}/{code}")
def panel_advice_detail(date: str, code: str, request: Request) -> HTMLResponse:
    """③⑤共用建议卡详情 fragment（四块布局 + rendered 原值 + σ√h 表）。"""
    from stock_trading_agent.server.panel import render_advice_detail

    body = advice_card(date, code, request)  # 复用 JSON 端点（404 语义同源）
    return HTMLResponse(render_advice_detail(body))


# --- L2 总闸（M3：面板⑦设置区；写者=主 Agent 进程内直调，12-orchestration §⑩） ---


@router.get("/l2")
def l2_state(request: Request) -> dict:
    from stock_trading_agent.state.agent_config import load_agent_config

    cfg = load_agent_config(_layout(request).agent_config_path)
    runtime = _runtime(request)
    return {
        "l2_enabled": cfg.l2_enabled,
        "billing_mode": cfg.billing_mode,
        "updated_at": cfg.updated_at,
        "budget_enabled": bool(runtime and runtime.loop.budget.enabled),
    }


class L2ToggleRequest(BaseModel):  # L2 开关请求契约（局部）
    enabled: bool


@router.post("/l2")
def l2_toggle(payload: L2ToggleRequest, request: Request) -> dict:
    from stock_trading_agent.state.agent_config import load_agent_config, save_agent_config
    from stock_trading_agent.state.store import FileLock

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(status_code=503, detail="运行时未持锁（L2 开关需 daemon 持有者）")
    layout = _layout(request)
    with FileLock(layout.queue_lock):  # _decide 锁内调用契约
        cfg = load_agent_config(layout.agent_config_path)
        cfg.l2_enabled = payload.enabled
        save_agent_config(layout.agent_config_path, cfg)
        cfg = runtime.apply_l2_config(probe=payload.enabled)  # 开启即探测认证态并联动预算闸
        runtime.loop.decisions.append(  # W1 唯一入口：绑定 path 落盘
            _now_iso(),
            "l2_toggled",
            {"enabled": payload.enabled, "by": "panel", "billing_mode": cfg.billing_mode},
        )
    return {
        "l2_enabled": cfg.l2_enabled,
        "billing_mode": cfg.billing_mode,
        "budget_enabled": runtime.loop.budget.enabled,
    }


@router.get("/panel/l2")
def panel_l2(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_settings_fragment

    return HTMLResponse(render_settings_fragment(l2_state(request)))


@router.post("/panel/l2")
def panel_l2_toggle(request: Request, enabled: str = Form(...)) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_settings_fragment

    payload = L2ToggleRequest(enabled=enabled.lower() in ("true", "1", "on"))
    body = l2_toggle(payload, request)
    return HTMLResponse(render_settings_fragment(body))


# --- 后验战绩⑥ + 晨报卡（M2） ---


@router.get("/stats")
def stats(request: Request) -> dict:
    from stock_trading_agent.server.panel_data import load_root_json
    from stock_trading_agent.server.stats import (
        aggregate_decisions,
        budget_curve,
        t1_review_hitrate,
    )

    layout = _layout(request)
    ledger_rows = load_root_json(layout.root / "ledger.agent.json") or {}
    lines = decisions(request, tail=200)
    return {
        "decisions": aggregate_decisions(lines),
        "budget_curve": budget_curve(list(sessions(request).get("sessions", {}).values())),
        "t1_review": t1_review_hitrate(lines, ledger_rows),
    }


@router.get("/panel/stats")
def panel_stats(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_stats_fragment

    return HTMLResponse(render_stats_fragment(stats(request)))


@router.get("/panel/briefing")
def panel_briefing(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_briefing_fragment

    return HTMLResponse(render_briefing_fragment(_briefing_payload(request)))


def _briefing_payload(request: Request) -> dict:

    from stock_trading_agent.loop.briefing import has_ack
    from stock_trading_agent.loop.monitor_events import market_today

    layout = _layout(request)
    day = market_today()
    cards = []
    day_dir = layout.briefing_dir / day
    if day_dir.exists():
        decision_lines = decisions(request, tail=200)
        for card_path in sorted(day_dir.glob("*.md")):
            code = card_path.stem
            cards.append(
                {
                    "code": code,
                    "ack": has_ack(decision_lines, date=day, code=code),
                    "md": card_path.read_text(encoding="utf-8"),
                }
            )
    return {"date": day, "cards": cards}


@router.post("/panel/briefing/ack")
def panel_briefing_ack(
    request: Request, date: str = Form(...), code: str = Form(...)
) -> HTMLResponse:
    """确认状态走 decision log（hash 链防篡改；降级只读态 503）。"""
    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.server.panel import render_briefing_fragment

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(status_code=503, detail="运行时未持锁（briefing_ack 需 daemon 持有者）")
    if date != market_today():
        raise HTTPException(status_code=422, detail="仅当日卡可确认")
    runtime.loop.decisions.append(  # W1 唯一入口：绑定 path 落盘
        _now_iso(), "briefing_ack", {"date": date, "code": code}
    )
    return HTMLResponse(render_briefing_fragment(_briefing_payload(request)))


# --- 模拟盘④（paper 视图 + 确认/驳回提交半边；写者=paper 引擎进程内直调，13-paper §⑥） ---


def _paper_view(request: Request) -> dict:
    from stock_trading_agent.paper import pending as paper_pending
    from stock_trading_agent.paper.tplus1 import available_lots

    layout = _layout(request)
    portfolio = paper_ledger_load(layout.sim_portfolio_path)
    pending_queue = paper_pending.load(layout.sim_pending_fills_path)
    today = market_today()
    positions = []
    if portfolio is not None:
        for pos in portfolio.positions:
            frozen = pending_queue.frozen_sell_lots(pos.code)
            sellable = (
                available_lots(portfolio, pos.code, today, pending_sell_lots=frozen)
                if pos.lots > 0
                else 0
            )
            positions.append(
                {
                    **pos.model_dump(),
                    "sellable_lots": sellable,  # 呈现态豁免：面板可显份额原值（13-paper §⑥）
                    "market_value": pos.lots * pos.board_lot * (pos.last_price or 0.0),
                }
            )
    from stock_trading_agent.server.stats import perf_summary

    return {
        "exists": portfolio is not None,
        "perf": (
            perf_summary(
                [e.model_dump() for e in portfolio.equity_history],
                [op.model_dump() for op in portfolio.operations],
            )
            if portfolio
            else None
        ),
        "positions": positions,
        "operations": ([op.model_dump() for op in portfolio.operations] if portfolio else []),
        "equity_history": [e.model_dump() for e in portfolio.equity_history] if portfolio else [],
        "corporate_actions": (
            [c.model_dump() for c in portfolio.corporate_actions] if portfolio else []
        ),
        "pending": [
            {k: v for k, v in vars(r).items() if k != "bar"} | {"bar_date": r.bar.date}
            for r in pending_queue.requests
        ],
        "cash": portfolio.cash if portfolio else 0.0,
        "budget": portfolio.budget if portfolio else 0.0,
    }


def paper_ledger_load(path):
    from stock_trading_agent.paper import ledger as paper_ledger

    return paper_ledger.load(path)


@router.get("/paper/view")
def paper_view(request: Request) -> dict:
    return _paper_view(request)


@router.post("/paper/confirm")
def paper_confirm(payload: dict, request: Request) -> dict:
    return _paper_decide(request, payload, action="confirm")


@router.post("/paper/reject")
def paper_reject(payload: dict, request: Request) -> dict:
    return _paper_decide(request, payload, action="reject")


def _paper_decide(request: Request, payload: dict, *, action: str) -> dict:
    """确认/驳回（sim.lock 内 pending+portfolio 读-改-写；确认走 ledger.apply_fill 唯一入口）。"""
    from stock_trading_agent.paper import ledger as paper_ledger
    from stock_trading_agent.paper import pending as paper_pending
    from stock_trading_agent.state.store import FileLock

    request_id = str(payload.get("request_id") or "")
    if not request_id:
        raise HTTPException(status_code=422, detail="request_id 必填")
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="运行时未挂载")
    layout = _layout(request)
    try:
        with FileLock(layout.sim_lock):
            queue = paper_pending.load(layout.sim_pending_fills_path)
            if queue.find(request_id) is None:
                raise HTTPException(status_code=404, detail=f"确认请求不存在：{request_id}")
            queue = queue.confirm(request_id) if action == "confirm" else queue.reject(request_id)
            if action == "confirm":
                req = queue.confirmed(request_id)
                portfolio = paper_ledger.load(layout.sim_portfolio_path)
                # 确认闸=锁内串行+重载（先到先得：并发同额后到者被持仓不足拒，13-paper §③）；
                # 在途卖出冻结（frozen_sell_lots）只进展示层 available_lots，不进确认闸
                portfolio = paper_ledger.apply_fill(portfolio, req)
                paper_ledger.persist(portfolio, layout.sim_portfolio_path)
                runtime.loop.decisions.append(
                    _now_iso(),
                    "paper_fill_confirmed",
                    {"request_id": request_id, "code": req.code, "leg": req.tranche_leg},
                )
            else:
                runtime.loop.decisions.append(
                    _now_iso(),
                    "paper_fill_rejected",
                    {"request_id": request_id, "code": queue.find(request_id).code},
                )
            # W1 唯一入口：decisions.append 经绑定 path 即时落盘，无旁路重写
            queue = queue.remove(request_id)
            paper_pending.save(queue, layout.sim_pending_fills_path)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:  # ConfirmError / LedgerError
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "request_id": request_id, "action": action}


@router.post("/panel/paper/confirm")
def panel_paper_confirm(request: Request, request_id: str = Form(...)) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_paper_fragment

    payload = {"request_id": request_id}
    resp = _paper_decide(request, payload, action="confirm")
    return HTMLResponse(
        render_paper_fragment(_paper_view(request), notice=f"已确认 {resp['request_id']}")
    )


@router.post("/panel/paper/reject")
def panel_paper_reject(request: Request, request_id: str = Form(...)) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_paper_fragment

    payload = {"request_id": request_id}
    resp = _paper_decide(request, payload, action="reject")
    return HTMLResponse(
        render_paper_fragment(_paper_view(request), notice=f"已驳回 {resp['request_id']}")
    )


@router.get("/panel/paper")
def panel_paper(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_paper_fragment

    return HTMLResponse(render_paper_fragment(_paper_view(request)))


def _now_iso() -> str:
    import datetime as dt

    return dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")


# --- 建议卡库（⑤：monitor/advice/ 只读投影 + 拒卡/降级通报区） ---


@router.get("/advice")
def advice(request: Request, date: str | None = None) -> dict:
    import datetime as dt

    layout = _layout(request)
    day = date or market_today()
    cards: list[dict] = []
    day_dir = layout.advice_dir / day
    if day_dir.exists():
        for card_path in sorted(day_dir.glob("*.md")):
            meta = parse_advice_card(card_path.read_text(encoding="utf-8"))
            if not meta:
                continue
            cards.append(
                {
                    "code": meta.get("code", card_path.stem),
                    "path": str(card_path),
                    "mtime": dt.datetime.fromtimestamp(
                        card_path.stat().st_mtime, tz=dt.UTC
                    ).isoformat(timespec="seconds"),
                    **{k: v for k, v in meta.items() if k != "task_id"},
                }
            )
    refused = _advice_refused(request, day)
    return {"date": day, "cards": cards, "refused": refused}


def _advice_refused(request: Request, day: str) -> list[dict]:
    """当日拒卡/降级通报（decisions 尾部扫描 kind=advice_refused，ts 市场钟面匹配）。"""
    import datetime as dt
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    out = []
    for line in decisions(request, tail=200):
        if line.get("kind") != "advice_refused":
            continue
        try:
            ts = dt.datetime.fromisoformat(line.get("ts", ""))
        except ValueError:
            continue
        if ts.astimezone(tz).strftime("%Y-%m-%d") != day:
            continue
        payload = line.get("payload") or {}
        out.append(
            {
                "code": payload.get("code", ""),
                "origin": payload.get("origin", ""),
                "reason_code": payload.get("reason_code", ""),
                "facts_note": payload.get("facts_note", ""),
                "ts": line.get("ts", ""),
            }
        )
    return out


@router.get("/advice/{date}/{code}")
def advice_card(date: str, code: str, request: Request) -> dict:
    layout = _layout(request)
    path = layout.advice_card_path(date, code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"建议卡不存在：{date}/{code}")
    md = path.read_text(encoding="utf-8")
    return {
        "code": code,
        "date": date,
        "md": md,
        "meta": parse_advice_card(md),
        # rendered：呈现态原值（12-orchestration §⑫；落盘 md 维持脱敏零改动）
        "rendered": _advice_rendered(request, code),
    }


def _advice_rendered(request: Request, code: str) -> dict | None:
    """运行时现算份额原值：sim-portfolio+tplus1（无持仓→None；落盘与负断言零改动）。"""
    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.paper import ledger as paper_ledger
    from stock_trading_agent.paper import pending as paper_pending
    from stock_trading_agent.paper.tplus1 import available_lots

    layout = _layout(request)
    portfolio = paper_ledger.load(layout.sim_portfolio_path)
    if portfolio is None:
        return None
    position = portfolio.position(code)
    if position is None or position.lots <= 0:
        return None
    queue = paper_pending.load(layout.sim_pending_fills_path)
    sellable = available_lots(
        portfolio, code, market_today(), pending_sell_lots=queue.frozen_sell_lots(code)
    )
    return {
        "held_lots": position.lots,
        "t1_sell_lots": sellable,
        "t1_sell_shares": sellable * position.board_lot,
        "board_lot": position.board_lot,
        "asof": market_today(),
    }


@router.get("/panel/advice")
def panel_advice(request: Request) -> HTMLResponse:
    from stock_trading_agent.server.panel import render_advice_library

    return HTMLResponse(render_advice_library(advice(request)))


# --- 提交半边（§11「读+提交命令」；写者=主 Agent 进程内直调 runtime.submit） ---


def _do_submit(request: Request, kind: str, code: str) -> tuple[dict | None, int, str]:
    runtime = _runtime(request)
    if runtime is None:
        return None, 503, "运行时未挂载（serve --enable-loop / create_app 缺省只读）"
    try:
        task = runtime.submit(kind=kind, code=code, source="panel")
    except LoopReject as exc:
        status = REJECT_STATUS.get(exc.reason, 500)
        return None, status, str(exc)
    return asdict(task), 201, ""


@router.post("/queue/submit", status_code=201)
def submit(payload: SubmitRequest, request: Request) -> dict:
    task, status, detail = _do_submit(request, payload.kind, payload.code)
    if task is None:
        raise HTTPException(status_code=status, detail=detail)
    return task


@router.post("/panel/submit")
def panel_submit(request: Request, kind: str = Form(...), code: str = Form(...)) -> HTMLResponse:
    try:
        payload = SubmitRequest(kind=kind, code=code)  # 同一白名单闸（kind/code 校验不因表单放宽）
    except ValidationError as exc:
        return HTMLResponse(
            render_submit_result(None, status=422, detail=str(exc.errors(include_url=False))),
            status_code=422,
        )
    task, status, detail = _do_submit(request, payload.kind, payload.code)
    return HTMLResponse(
        render_submit_result(task, status=status, detail=detail), status_code=status
    )


# --- HTML fragment（旧版 overview 已被 §⑫ 新七页端点族取代）


@router.get("/panel/events")
def panel_events(request: Request) -> HTMLResponse:
    return HTMLResponse(render_events_table(_monitor_events_payload(request, date=None, tail=50)))


@router.get("/panel/audit")
def panel_audit(request: Request) -> HTMLResponse:
    return HTMLResponse(render_audit(decisions(request, tail=20), budget(request)))


@router.get("/panel/task/{task_id}")
def panel_task(task_id: str, request: Request) -> HTMLResponse:
    q = queue(request)
    task = next((t for t in q.get("tasks", []) if t.get("task_id") == task_id), None)
    if task is None:
        return HTMLResponse(
            '<div class="task-card" id="task-card"><p class="warn">任务不存在</p></div>'
        )
    return HTMLResponse(render_task_card(task))
