"""面板路由：只读态 + 提交半边 + monitor/reports 只读投影 + HTML fragment（§11/§16）。

写动词 POST /api/queue/submit 与 /api/panel/submit 经进程内 LoopRuntime.submit（写者=主
Agent，§11「读+提交命令」）；全部端点 def（阻塞 IO 走线程池，I-38）。
NR-5：monitor events/universe 本身即脱敏产物（公开行情+规则事实），透出合规；
postmortem 槽探测只读 ledger.json mtime（本层可引用，state/ 层禁——红线扫描机器门）。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError

from stock_trading_agent import __version__
from stock_trading_agent.loop.advice import has_review, parse_advice_card
from stock_trading_agent.loop.briefing import has_ack
from stock_trading_agent.loop.events import MARKET_TZ, lock_is_held
from stock_trading_agent.loop.loop import LoopReject
from stock_trading_agent.loop.monitor_events import market_today, parse_line
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper import pending as paper_pending
from stock_trading_agent.paper.corporate_actions import (
    DividendAction,
    SplitAction,
    apply_corporate_action,
)
from stock_trading_agent.paper.tplus1 import available_lots
from stock_trading_agent.server.panel import (
    _ts_hhmm,
    render_advice_detail,
    render_advice_library,
    render_advice_zone_empty,
    render_alloc_form,
    render_audit,
    render_audit_archive,
    render_audit_transcript,
    render_briefing_fragment,
    render_ca_form,
    render_calendar,
    render_diag_form,
    render_event_feed,
    render_events_table,
    render_navcard,
    render_notifications,
    render_paper_fragment,
    render_phase_chip,
    render_report_body,
    render_reports_list,
    render_session_obs,
    render_sessions_table,
    render_settings_fragment,
    render_signals_table,
    render_stats_fragment,
    render_submit_result,
    render_syscard,
    render_task_card,
    render_tasks_table,
    render_todos,
)
from stock_trading_agent.server.panel_data import (
    PriceFact,
    aggregate_calendar,
    build_name_map,
    derive_todos,
    latest_advice_reference,
    load_name_map,
    load_root_json,
    market_phase,
    merge_price_facts,
    parse_ticks_prices,
    rebuild_notifications,
    signals_snapshot,
    t1_price,
)
from stock_trading_agent.server.schemas import (
    REJECT_STATUS,
    CorporateActionRequest,
    SubmitRequest,
)
from stock_trading_agent.server.session_products import (
    opinions_board,
    read_session_products,
    stage_status,
    verdict_card,
)
from stock_trading_agent.server.stats import (
    aggregate_decisions,
    assertion_buckets,
    budget_curve,
    cost_by_kind,
    paper_totals,
    perf_summary,
    route_hit_rate,
    t1_review_hitrate,
)
from stock_trading_agent.state.agent_config import load_agent_config, save_agent_config
from stock_trading_agent.state.decisions import DecisionChainError, DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import QueueState
from stock_trading_agent.state.sessions import SessionsState
from stock_trading_agent.state.store import (
    FileLock,
    read_json,
    read_json_cached,
    read_jsonl_lines_cached,
    read_text_cached,
)

router = APIRouter(prefix="/api")
root_router = APIRouter()  # 无前缀（favicon 等根级路由）

# 组件身份 SSOT：health 端点与 serve 端口预检（app.probe_panel）同源，防两处字面量漂移
PANEL_COMPONENT = "stock-trading-agent-panel"

_REPORTS_DIR_RE = re.compile(r"^\d{5,6}\.(cn|hk)$")
_ADVICE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 建议卡导出三保险之一（0.0.25）
_ADVICE_CODE_RE = re.compile(r"^\d{5,6}$")


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
    return {"ok": True, "component": PANEL_COMPONENT, "version": __version__}


@router.get("/queue")
def queue(request: Request) -> dict:

    data = read_json_cached(_layout(request).queue_path)
    return data if data is not None else QueueState().to_dict()


@router.get("/sessions")
def sessions(request: Request) -> dict:

    data = read_json_cached(_layout(request).sessions_path)
    return data if data is not None else SessionsState().to_dict()


@router.get("/decisions")
def decisions(request: Request, tail: int = Query(default=20, ge=1, le=200)) -> list[dict]:

    lines = [json.loads(raw) for raw in read_jsonl_lines_cached(_layout(request).decisions_path)]
    return lines[-tail:]


@router.get("/paper")
def paper(request: Request) -> dict:

    layout = _layout(request)
    portfolio = paper_ledger.load(layout.sim_portfolio_path)
    if portfolio is None:
        return {"exists": False, "positions": [], "operations": []}
    return {"exists": True, **portfolio.model_dump()}


@router.get("/budget")
def budget(request: Request) -> dict:

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
            "chain_error": (runtime.chain_error if runtime is not None else None),
        },
    }


def _mtime_day(path: Path) -> str:
    return (
        dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
        .astimezone(MARKET_TZ)
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
            # latest 排除 index.md（字典序恒压日期名——0.0.24 修「最新报告恒 index.md」）
            dated = [p for p in files if p != "index.md"] or ["index.md"]
            out.append(
                {
                    "code": code,
                    "market": market,
                    "dir": d.name,
                    "latest": dated[-1],
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

    layout = _layout(request)

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

    layout = _layout(request)
    events = _monitor_events_payload(request, date=None, tail=500).get("events", [])

    log_text = read_text_cached(layout.monitor_dir / "logs" / "watch.out.log")
    names = load_name_map(layout)
    rows = rebuild_notifications(events, log_text, names)
    return rows[-tail:]


def _guarded_file(
    root: Path, parts: tuple[str, ...], *, part_res: tuple[str, ...], bad: str, missing: str
) -> Path:
    """路径三保险 SSOT：逐段正则白名单 + resolve 边界 + 存在性；任一不过即 404（文案站点给）。"""
    if not all(re.fullmatch(pat, p) for pat, p in zip(part_res, parts, strict=True)):
        raise HTTPException(status_code=404, detail=bad)
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root.resolve()) or not path.exists():
        raise HTTPException(status_code=404, detail=missing)
    return path


# --- 面板新端点（0.0.11：signals/todos/topbar/phase/报告与建议卡 fragment，全 GET） ---


@router.get("/signals")
def signals(request: Request) -> dict:
    """在册信号快照（signals.json 只读 + 三源现价合并，§⑫ 现价源升级位 0.0.25）。"""

    layout = _layout(request)
    entries = load_root_json(layout.root / "signals.json")

    today = market_today()
    # 源 1：sim 持仓确认 bar 价（asof=last_price_date，可缺席）
    sim_map: dict[str, PriceFact] = {}

    portfolio = paper_ledger.load(layout.sim_portfolio_path)
    if portfolio:
        for pos in portfolio.positions:
            if pos.last_price:
                sim_map[pos.code] = PriceFact(
                    price=float(pos.last_price),
                    asof=str(getattr(pos, "last_price_date", "") or ""),
                    source="sim",
                )
    # 源 2：盯盘事件 facts 价（事件时点价，asof=事件 ts）
    events_map: dict[str, PriceFact] = {}
    for ev in _monitor_events_payload(request, date=None, tail=500).get("events", []):
        facts = ev.get("facts") or {}
        price = facts.get("price")
        if price is not None and ev.get("code"):
            events_map[str(ev["code"])] = PriceFact(
                price=float(price), asof=str(ev.get("ts") or ""), source="events"
            )
    # 源 3：monitor/ticks 轮询价（现价真实落点；watch.py 写者，产品仓只读）
    ticks_map: dict[str, PriceFact] = {}
    ticks_path = layout.monitor_dir / "ticks" / f"{today}.jsonl"
    if ticks_path.exists():
        try:
            ticks_map = parse_ticks_prices(ticks_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            ticks_map = {}
    # 源 4：T1 evidence 取证价（blocks.quote.price，state=available 才取）
    t1_map: dict[str, PriceFact] = {}
    t1_root = layout.runs_dir / today / "t1"
    if t1_root.exists():
        for d in sorted(t1_root.iterdir()):
            ev = load_root_json(d / "evidence.json")
            fact = t1_price(ev) if isinstance(ev, dict) else None
            if fact and d.name.isdigit():
                t1_map[d.name] = fact
    price_map = merge_price_facts(sim_map, events_map, ticks_map, t1_map)
    snap = signals_snapshot(
        entries if isinstance(entries, list) else None, price_map, market_date=today
    )
    snap["name_map"] = build_name_map(reports(request).get("reports", []))
    snap["names"] = load_name_map(layout, signals=entries)  # R4：signals 单请求单读（原先 2 次）
    return snap


@router.get("/todos")
def todos(request: Request) -> dict:
    """今日待办三源聚合（pending fills → mech_risk 事件 → briefing 未 ack）。"""

    layout = _layout(request)
    events = _monitor_events_payload(request, date=None, tail=500).get("events", [])

    pending = [
        vars(r) | {"bar_date": r.bar.date}
        for r in paper_pending.load(layout.sim_pending_fills_path).requests
    ]
    brief = _briefing_payload(request).get("cards", [])
    return {"todos": derive_todos(events, pending, brief)}


@router.get("/panel/phase")
def panel_phase(request: Request) -> HTMLResponse:
    """顶栏右侧：日期周几 + 盘态 pill。"""

    return HTMLResponse(render_phase_chip(market_phase()))


@router.get("/panel/topbar")
def panel_topbar(request: Request) -> HTMLResponse:
    """①页 hero 右：刷新时刻 + 事件/待办计数带（盘态唯一呈现源=顶栏，0.0.24 去重）。"""
    now = dt.datetime.now(tz=dt.UTC).astimezone(MARKET_TZ)
    events = _monitor_events_payload(request, date=None, tail=50).get("events") or []
    todo_items = todos(request).get("todos") or []
    return HTMLResponse(
        f'<span class="metachip" title="SSE 实时流 + 180s 轮询兜底">刷新 {now:%H:%M:%S}</span>'
        f'<span class="metachip">事件 {len(events)} · 待办 {len(todo_items)}</span>'
    )


@router.get("/panel/signals")
def panel_signals(request: Request) -> HTMLResponse:

    return HTMLResponse(render_signals_table(signals(request)))


@router.get("/session/{task_id}")
def session_view(task_id: str, request: Request) -> dict:
    """会话中间产物视图（queue 反查 session_uuid → .vs 四件解析；P2 §⑫ 案 a）。"""

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

    return HTMLResponse(render_session_obs(session_view(task_id, request)))


@router.get("/panel/calendar")
def panel_calendar(request: Request) -> HTMLResponse:

    return HTMLResponse(render_calendar(calendar(request)))


@router.get("/panel/notifications")
def panel_notifications(request: Request) -> HTMLResponse:

    return HTMLResponse(render_notifications({"items": notifications(request, tail=10)}))


@router.get("/panel/todos")
def panel_todos(request: Request) -> HTMLResponse:

    return HTMLResponse(render_todos(todos(request)))


@router.get("/panel/navcard")
def panel_navcard(request: Request) -> HTMLResponse:

    return HTMLResponse(render_navcard(_paper_view(request)))


@router.get("/panel/syscard")
def panel_syscard(request: Request) -> HTMLResponse:

    return HTMLResponse(
        render_syscard(
            monitor_status(request), sessions(request), queue(request), l2_state(request)
        )
    )


@router.get("/panel/overview")
def panel_overview(request: Request) -> HTMLResponse:
    """①事件流卡体 + hero 计数带 oob 下发（ov-sub/ov-todo-count）。

    #ov-events 消费主体（innerHTML）；ov-ev-count 由 feed 片段尾部单源自带（0.0.35 收口
    同响应双发射——顶层不再重复，htmx 对任意 swap 均处理 oob）。
    """
    payload = _monitor_events_payload(request, date=None, tail=50)
    payload["names"] = load_name_map(_layout(request))
    events = payload.get("events") or []
    downgraded = sum(1 for ev in events if ev.get("mech_risk"))
    todo_items = todos(request).get("todos") or []
    phase = market_phase()
    sub = (
        f"{phase.get('label', '')} · 事件 {len(events)} 条"
        + (f"（{downgraded} 条降档高亮）" if downgraded else "")
        + f" · 待办 {len(todo_items)} 项"
    )
    body = render_event_feed(payload)
    return HTMLResponse(
        body
        + f'<div id="ov-sub" hx-swap-oob="true" class="sub">{html.escape(sub)}</div>'
        + f'<span id="ov-todo-count" hx-swap-oob="true" class="cm">{len(todo_items)} 项</span>'
    )


@router.get("/panel/mometa")
def panel_mometa(request: Request) -> HTMLResponse:
    """③页 hero 右：watch 心跳 + 零 LLM 标识。"""
    status = monitor_status(request)
    watch_state = status.get("watch_state") or {}
    next_due = (watch_state.get("next_due") or {}).get("cn") or ""
    beat = _ts_hhmm(next_due) if next_due else "—"
    return HTMLResponse(
        f'<span class="metachip" title="watch 下一心跳（cn）：{html.escape(next_due)}">'
        f"watch 心跳 {html.escape(beat)}</span>"
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


def _chain_state(request: Request) -> bool | None:
    """链校验态（panel_audit 与 aumeta 共源；DecisionChainError 降级 False）。"""

    try:
        log = DecisionLog.load(_layout(request).decisions_path)
        return bool(log.verify_chain())
    except DecisionChainError:
        return False


@router.get("/panel/aumeta")
def panel_aumeta(request: Request) -> HTMLResponse:
    """⑦页 hero 右：hash 链 head + 链校验态 chip（0.0.24 与台账卡首摘要联动）。"""
    lines = decisions(request, tail=1)
    head = (lines[-1].get("hash", "")[:12] + "…") if lines else "空链"
    chain_ok = _chain_state(request)
    chain_chip = (
        '<span class="metachip">链校验 通过</span>'
        if chain_ok
        else '<span class="riskchip">链校验 失败</span>'
    )
    return HTMLResponse(f'<span class="metachip">hash 链 head {head}</span>{chain_chip}')


@router.get("/panel/dgform")
def panel_dgform(request: Request) -> HTMLResponse:
    """②页发起诊股表单（六 kind 全量下拉 + 持仓成本说明位）。"""

    return HTMLResponse(render_diag_form())


@router.get("/panel/tasks")
def panel_tasks(request: Request) -> HTMLResponse:

    return HTMLResponse(render_tasks_table(queue(request)))


@router.get("/panel/reports")
def panel_reports(request: Request) -> HTMLResponse:

    return HTMLResponse(render_reports_list(reports(request)))


@router.get("/panel/report/{code_market}/{filename}")
def panel_report_body(code_market: str, filename: str, request: Request) -> HTMLResponse:
    """②报告正文 fragment（三保险路径安全：目录正则+日期文件名正则+resolve 边界）。"""

    path = _guarded_file(
        _layout(request).reports_dir,
        (code_market, filename),
        part_res=(r"\d{5,6}\.(cn|hk)", r"(?:\d{4}-\d{2}-\d{2}|index)\.md"),
        bad="报告路径非法",
        missing="报告不存在",
    )
    return HTMLResponse(render_report_body(code_market, filename, path.read_text(encoding="utf-8")))


@router.get("/panel/advice-detail/{date}/{code}")
def panel_advice_detail(
    date: str, code: str, request: Request, mount: str = Query(default="ad")
) -> HTMLResponse:
    """③⑤共用建议卡详情 fragment（四块布局 + rendered 原值 + σ√h 表）。

    mount=mo→③页 #advice-detail / ad→⑤页 #advice-detail-2（按钮 target 按挂点映射；
    白名单外回落 ad 不 422——呈现参数非安全参数）。
    """

    body = advice_card(date, code, request)  # 复用 JSON 端点（404 语义同源）
    return HTMLResponse(render_advice_detail(body, mount=mount if mount in ("mo", "ad") else "ad"))


@router.get("/panel/advice-detail/latest")
def panel_advice_detail_latest(request: Request, mount: str = Query(default="mo")) -> HTMLResponse:
    """③页复核区默认态：自动注入最新待复核建议卡（0.0.37；无卡回空态卡不 404）。"""
    # 复核判定喂全量 decisions 行（门6 F-1：tail=200 尾窗实测仅 ~2.5 天 < 解析器 5 日扫描
    # 窗，旧卡复核态误判会令默认注入错选并复活已复核卡按钮——读侧无 API 契约约束，全量廉价）
    all_lines = [
        json.loads(raw) for raw in read_jsonl_lines_cached(_layout(request).decisions_path)
    ]
    ref = latest_advice_reference(_layout(request).advice_dir, all_lines)
    if ref is None:
        return HTMLResponse(render_advice_zone_empty())
    date, code = ref
    body = advice_card(date, code, request)
    return HTMLResponse(render_advice_detail(body, mount=mount if mount in ("mo", "ad") else "mo"))


# --- L2 总闸（M3：面板⑦设置区；写者=主 Agent 进程内直调，12-orchestration §⑩） ---


@router.get("/l2")
def l2_state(request: Request) -> dict:

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

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(status_code=503, detail="运行时未持锁（L2 开关需 daemon 持有者）")
    layout = _layout(request)
    # apply_l2_config 内部 _decide 亦可能抛断链（503 语义保持；留痕自身经 _record_decision 收敛）
    try:
        with FileLock(layout.queue_lock):  # _decide 锁内调用契约
            cfg = load_agent_config(layout.agent_config_path)
            cfg.l2_enabled = payload.enabled
            save_agent_config(layout.agent_config_path, cfg)
            cfg = runtime.apply_l2_config(probe=payload.enabled)  # 开启即探测认证态并联动预算闸
            _record_decision(
                runtime,
                layout,
                "l2_toggled",
                {"enabled": payload.enabled, "by": "panel", "billing_mode": cfg.billing_mode},
            )
    except DecisionChainError as exc:
        raise HTTPException(status_code=503, detail=f"{exc}") from exc
    return {
        "l2_enabled": cfg.l2_enabled,
        "billing_mode": cfg.billing_mode,
        "budget_enabled": runtime.loop.budget.enabled,
    }


@router.get("/panel/l2")
def panel_l2(request: Request) -> HTMLResponse:

    return HTMLResponse(render_settings_fragment(l2_state(request)))


@router.post("/panel/l2")
def panel_l2_toggle(request: Request, enabled: str = Form(...)) -> HTMLResponse:

    payload = L2ToggleRequest(enabled=enabled.lower() in ("true", "1", "on"))
    body = l2_toggle(payload, request)
    return HTMLResponse(render_settings_fragment(body))


# --- 后验战绩⑥ + 晨报卡（M2） ---


@router.get("/stats")
def stats(request: Request) -> dict:

    layout = _layout(request)
    ledger_rows = load_root_json(layout.root / "ledger.agent.json") or {}
    lines = decisions(request, tail=200)
    session_records = list(sessions(request).get("sessions", {}).values())
    return {
        "decisions": aggregate_decisions(lines),
        "budget_curve": budget_curve(session_records),
        "t1_review": t1_review_hitrate(lines, ledger_rows),
        "assertion_buckets": assertion_buckets(lines),  # §15 断言违例率分桶（0.0.22）
        "route_hit": route_hit_rate(lines),  # §15 意图路由命中率
        "cost_by_kind": cost_by_kind(
            session_records, (read_json(layout.queue_path) or {}).get("tasks", [])
        ),
    }


@router.get("/panel/stats")
def panel_stats(request: Request) -> HTMLResponse:

    return HTMLResponse(render_stats_fragment(stats(request)))


@router.get("/panel/briefing")
def panel_briefing(request: Request) -> HTMLResponse:

    return HTMLResponse(render_briefing_fragment(_briefing_payload(request)))


def _briefing_payload(request: Request) -> dict:

    layout = _layout(request)
    day = market_today()
    cards = []
    day_dir = layout.briefing_dir / day
    if day_dir.exists():
        decision_lines = decisions(request, tail=200)
        for card_path in sorted(day_dir.glob("*.md")):
            # 槽位解析（0.0.18）：<code>.am.md / <code>.pm.md / legacy <code>.md
            stem = card_path.stem
            code, _, slot = stem.partition(".")
            if slot not in ("am", "pm"):
                code, slot = stem, ""
            cards.append(
                {
                    "code": code,
                    "session": slot,
                    "ack": has_ack(decision_lines, date=day, code=code, slot=slot),
                    "md": card_path.read_text(encoding="utf-8"),
                }
            )
    # 关键价位 kchip（①晨报区）：signals key_levels 透传——止损恒显、买点/MA20 有键才显

    key_levels: dict[str, dict] = {}
    entries = load_root_json(layout.root / "signals.json")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("code"):
                levels = entry.get("key_levels") or {}
                key_levels[str(entry["code"])] = {
                    k: levels.get(k) for k in ("stop", "buy", "ma20") if levels.get(k) is not None
                }
    return {"date": day, "cards": cards, "key_levels": key_levels}


@router.post("/panel/briefing/ack")
def panel_briefing_ack(
    request: Request,
    date: str = Form(...),
    code: str = Form(...),
    session: str = Form(""),
) -> HTMLResponse:
    """确认状态走 decision log（hash 链防篡改；降级只读态 503；session 槽位 0.0.18）。"""

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(status_code=503, detail="运行时未持锁（briefing_ack 需 daemon 持有者）")
    if date != market_today():
        raise HTTPException(status_code=422, detail="仅当日卡可确认")
    layout = _layout(request)
    if session not in ("", "am", "pm"):
        raise HTTPException(status_code=422, detail="session 仅允许空/am/pm")
    _record_decision(
        runtime,
        layout,
        "briefing_ack",
        {"date": date, "code": code, **({"session": session} if session else {})},
    )
    return HTMLResponse(render_briefing_fragment(_briefing_payload(request)))


@router.post("/panel/advice/review")
def panel_advice_review(
    request: Request,
    date: str = Form(...),
    code: str = Form(...),
    mount: str = Form("ad"),
) -> HTMLResponse:
    """T2 建议卡「标记已复核」：advice_reviewed 决策（hash 链留痕；0.0.25 原型对账项）。

    幂等语义沿 briefing_ack（重复 POST 再记一条、呈现取存在即已复核、不 409）；
    payload 恰 {date, code}（NR-5 零敏感量）。卡不在场 404（端点可独立调用，比
    briefing_ack 收严——其卡列表先渲染故免检）。
    """

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(
            status_code=503, detail="运行时未持锁（advice_reviewed 需 daemon 持有者）"
        )
    if date != market_today():
        raise HTTPException(status_code=422, detail="仅当日卡可标记已复核")
    if not _ADVICE_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail="code 仅允许 5/6 位数字")
    layout = _layout(request)
    if not layout.advice_card_path(date, code).exists():
        raise HTTPException(status_code=404, detail=f"建议卡不存在：{date}/{code}")
    _record_decision(runtime, layout, "advice_reviewed", {"date": date, "code": code})
    m = mount if mount in ("mo", "ad") else "ad"
    return HTMLResponse(render_advice_detail(advice_card(date, code, request), mount=m))


# --- 模拟盘④（paper 视图 + 确认/驳回提交半边；写者=paper 引擎进程内直调，13-paper §⑥） ---


def _paper_view(request: Request) -> dict:

    layout = _layout(request)
    portfolio = paper_ledger.load(layout.sim_portfolio_path)
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
        "names": load_name_map(layout),
        "totals": paper_totals(
            positions,
            portfolio.cash if portfolio else 0.0,
            portfolio.budget if portfolio else 0.0,
        ),
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
                _record_decision(
                    runtime,
                    layout,
                    "paper_fill_confirmed",
                    {"request_id": request_id, "code": req.code, "leg": req.tranche_leg},
                )
            else:
                _record_decision(
                    runtime,
                    layout,
                    "paper_fill_rejected",
                    {"request_id": request_id, "code": queue.find(request_id).code},
                )
            queue = queue.remove(request_id)
            paper_pending.save(queue, layout.sim_pending_fills_path)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:  # ConfirmError / LedgerError
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "request_id": request_id, "action": action}


def _corporate_action_decide(request: Request, data: dict) -> dict:
    """公司行动手动馈送（13-paper §④ M2 承诺闭合；sim.lock 内引擎直调，写者=paper 引擎）。

    幂等：corporate_actions 登记中同 code+kind+ex_date+口径（split 比率/dividend 每股额）→ 409；
    同日除权除息先送转后分红由调用方保序（表单文案与 spec §④ 提示）；
    决策 kind paper_corporate_action payload 零敏感量（NR-5：每股额/税/找零不进）。
    """
    from pydantic import ValidationError

    try:
        payload = CorporateActionRequest(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc.errors(include_url=False))) from exc
    if payload.kind == "split":
        if not (payload.ratio_num and payload.ratio_den and payload.ratio_num > payload.ratio_den):
            raise HTTPException(
                status_code=422, detail="split 需 ratio_num>ratio_den>=1（10送5 → 15/10）"
            )
    else:
        if not (payload.pay_date and payload.per_share_cash):
            raise HTTPException(status_code=422, detail="dividend 需 pay_date 与 per_share_cash>0")
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="运行时未挂载")
    layout = _layout(request)
    try:
        with FileLock(layout.sim_lock):
            portfolio = paper_ledger.load(layout.sim_portfolio_path)
            if portfolio is None:
                raise HTTPException(status_code=422, detail="模拟账本未建（无公司行动可馈送）")
            position = portfolio.position(payload.code)
            if position is None or position.lots <= 0:
                raise HTTPException(status_code=422, detail=f"无在持仓位：{payload.code}")
            for rec in portfolio.corporate_actions:
                if (
                    rec.code == payload.code
                    and rec.kind == payload.kind
                    and rec.ex_date == payload.ex_date
                    and rec.ratio_num == payload.ratio_num
                    and rec.ratio_den == payload.ratio_den
                    and rec.per_share_cash == payload.per_share_cash
                ):
                    raise HTTPException(status_code=409, detail="同口径公司行动已登记（幂等拒）")
            action: SplitAction | DividendAction = (
                SplitAction(
                    code=payload.code,
                    ratio_num=payload.ratio_num,
                    ratio_den=payload.ratio_den,
                    ex_date=payload.ex_date,
                )
                if payload.kind == "split"
                else DividendAction(
                    code=payload.code,
                    per_share_cash=payload.per_share_cash,
                    ex_date=payload.ex_date,
                    pay_date=payload.pay_date,
                )
            )
            portfolio = apply_corporate_action(portfolio, action)
            paper_ledger.persist(portfolio, layout.sim_portfolio_path)
            _record_decision(
                runtime,
                layout,
                "paper_corporate_action",
                {
                    "code": payload.code,
                    "action_kind": payload.kind,
                    "ex_date": payload.ex_date,
                    **(
                        {"ratio_num": payload.ratio_num, "ratio_den": payload.ratio_den}
                        if payload.kind == "split"
                        else {}
                    ),
                },
            )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "code": payload.code,
        "action_kind": payload.kind,
        "ex_date": payload.ex_date,
    }


@router.post("/paper/corporate-action")
def paper_corporate_action(payload: CorporateActionRequest, request: Request) -> dict:
    return _corporate_action_decide(request, payload.model_dump())


@router.post("/panel/paper/corporate-action")
def panel_paper_corporate_action(
    request: Request,
    kind: str = Form(...),
    code: str = Form(...),
    ex_date: str = Form(...),
    pay_date: str = Form(""),
    ratio_num: str = Form(""),
    ratio_den: str = Form(""),
    per_share_cash: str = Form(""),
) -> HTMLResponse:
    """htmx 表单变体：空串可选字段归一 None 后走同一闸。

    0.0.24 成功回窄通知卡（落 #paper-ca-result）——④三表刷新由 sse:paper/sse:multi
    驱动 #paper 重拉（写 sim-portfolio.json 必发事件；SSE 断连 180s 轮询兜底）。
    """
    data = {
        "kind": kind,
        "code": code,
        "ex_date": ex_date,
        "pay_date": pay_date or None,
        "ratio_num": int(ratio_num) if ratio_num else None,
        "ratio_den": int(ratio_den) if ratio_den else None,
        "per_share_cash": float(per_share_cash) if per_share_cash else None,
    }
    resp = _corporate_action_decide(request, data)
    notice = f"公司行动已入账：{resp['action_kind']} {resp['code']} @ {resp['ex_date']}"
    tip = "表格刷新由事件驱动（SSE 断连时最迟 180s 轮询兜底）"
    return HTMLResponse(f'<p class="ok" title="{html.escape(tip)}">{html.escape(notice)}</p>')


@router.get("/panel/paper/ca-form")
def panel_paper_ca_form(request: Request) -> HTMLResponse:
    """④公司行动表单（load-only 外置容器：轮询重渲不再擦输入态，0.0.24）。"""

    view = _paper_view(request)
    return HTMLResponse(render_ca_form(has_positions=bool(view.get("positions"))))


# --- allocation 导入（0.0.31：三批建仓主链路用户入口；06 Stage D 产物零格式发明） ---


def _allocation_decide(request: Request, data: dict) -> dict:
    """allocation.json → 三批建仓（sim.lock 内引擎直调；dup code 整批拒）。

    决策 paper_allocation_imported payload 零价格零数量（NR-5：仅 date 与 code 列表）；
    文件路径校验：存在 + .json 后缀 + ≤1MB（防误喂大文件；本地产品形态）。
    """
    import json as _json

    layout = _layout(request)
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(status_code=503, detail="运行时未挂载")
    path_str = str(data.get("file") or "").strip()
    if not path_str:
        raise HTTPException(status_code=422, detail="file 必填（allocation.json 路径）")
    path = Path(path_str).expanduser()
    if not path.is_file() or path.suffix != ".json" or path.stat().st_size > 1_048_576:
        raise HTTPException(status_code=422, detail=f"文件不可用或非 .json（≤1MB）：{path_str}")
    try:
        alloc = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"allocation 解析失败：{exc}") from exc
    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.paper.allocation import (
        AllocationError,
        import_allocation,
        new_portfolio_from_allocation,
        parse_allocation,
    )

    try:
        rows = parse_allocation(alloc)
    except AllocationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    date = str(data.get("date") or "").strip() or market_today()
    with FileLock(layout.sim_lock):
        portfolio = paper_ledger.load(layout.sim_portfolio_path)
        try:
            portfolio = (
                new_portfolio_from_allocation(alloc, opened_date=date)
                if portfolio is None
                else import_allocation(portfolio, rows, opened_date=date)
            )
        except ValueError as exc:  # LedgerError/AllocationError 族
            raise HTTPException(status_code=409, detail=f"整批拒绝（账本未变）：{exc}") from exc
        paper_ledger.persist(portfolio, layout.sim_portfolio_path)
    codes = [r.code for r in rows]
    _record_decision(runtime, layout, "paper_allocation_imported", {"date": date, "codes": codes})
    return {"ok": True, "date": date, "codes": codes}


@router.post("/paper/allocation")
def paper_allocation(payload: dict, request: Request) -> dict:
    return _allocation_decide(request, payload)


@router.post("/panel/paper/allocation")
def panel_paper_allocation(
    request: Request, file: str = Form(...), date: str = Form("")
) -> HTMLResponse:
    """htmx 表单变体：成功窄通知（落 #paper-alloc-result；④持仓表由 sse:paper 驱动重拉）。"""

    import html as _html

    resp = _allocation_decide(request, {"file": file, "date": date})
    notice = f"已导入 {len(resp['codes'])} 只三批建仓（{resp['date']}；T1 批其后首个交易日判定）"
    tip = "持仓表刷新由事件驱动（SSE 断连时最迟 180s 轮询兜底）"
    return HTMLResponse(f'<p class="ok" title="{_html.escape(tip)}">{_html.escape(notice)}</p>')


@router.get("/panel/paper/alloc-form")
def panel_paper_alloc_form(request: Request) -> HTMLResponse:
    """④allocation 导入表单（load-only 外置容器，沿 ca-form 先例）。"""

    portfolio = paper_ledger.load(_layout(request).sim_portfolio_path)
    return HTMLResponse(render_alloc_form(has_ledger=portfolio is not None))


@router.post("/panel/paper/confirm")
def panel_paper_confirm(request: Request, request_id: str = Form(...)) -> HTMLResponse:

    payload = {"request_id": request_id}
    resp = _paper_decide(request, payload, action="confirm")
    return HTMLResponse(
        render_paper_fragment(_paper_view(request), notice=f"已确认 {resp['request_id']}")
    )


@router.post("/panel/paper/reject")
def panel_paper_reject(request: Request, request_id: str = Form(...)) -> HTMLResponse:

    payload = {"request_id": request_id}
    resp = _paper_decide(request, payload, action="reject")
    return HTMLResponse(
        render_paper_fragment(_paper_view(request), notice=f"已驳回 {resp['request_id']}")
    )


@router.get("/panel/paper")
def panel_paper(request: Request) -> HTMLResponse:

    return HTMLResponse(render_paper_fragment(_paper_view(request)))


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")


def _record_decision(runtime, layout: StateLayout, kind: str, payload: dict) -> None:
    """决策留痕 SSOT（hash 链 append + 断链降级 503；写侧决策链断即拒写服务）。"""
    try:
        runtime.loop.decisions.append(_now_iso(), kind, payload, path=layout.decisions_path)
    except DecisionChainError as exc:
        raise HTTPException(status_code=503, detail=f"{exc}") from exc


# --- 建议卡库（⑤：monitor/advice/ 只读投影 + 拒卡/降级通报区） ---


@router.get("/advice")
def advice(request: Request, date: str | None = None) -> dict:
    layout = _layout(request)
    day = date or market_today()
    decision_lines = decisions(request, tail=200)
    cards: list[dict] = []
    day_dir = layout.advice_dir / day
    if day_dir.exists():
        for card_path in sorted(day_dir.glob("*.md")):
            meta = parse_advice_card(card_path.read_text(encoding="utf-8"), stem=card_path.stem)
            if not meta:
                continue
            code = meta.get("code", card_path.stem)
            cards.append(
                {
                    "code": code,
                    "path": str(card_path),
                    "mtime": dt.datetime.fromtimestamp(
                        card_path.stat().st_mtime, tz=dt.UTC
                    ).isoformat(timespec="seconds"),
                    "reviewed": has_review(decision_lines, date=day, code=code),
                    **{k: v for k, v in meta.items() if k != "task_id"},
                }
            )
    refused = _advice_refused(request, day)
    return {"date": day, "cards": cards, "refused": refused}


def _advice_refused(request: Request, day: str) -> list[dict]:
    """当日拒卡/降级通报（decisions 尾部扫描 kind=advice_refused，ts 市场钟面匹配）。"""

    out = []
    for line in decisions(request, tail=200):
        if line.get("kind") != "advice_refused":
            continue
        try:
            ts = dt.datetime.fromisoformat(line.get("ts", ""))
        except ValueError:
            continue
        if ts.astimezone(MARKET_TZ).strftime("%Y-%m-%d") != day:
            continue
        payload = line.get("payload") or {}
        local_ts = ts.astimezone(MARKET_TZ)
        out.append(
            {
                "code": payload.get("code", ""),
                "origin": payload.get("origin", ""),
                "reason_code": payload.get("reason_code", ""),
                # 写侧多数路径只带 reasons 数组（gate/assert/render_scan/write_failure），
                # facts_note 仅 session_degraded 路径在场——回退拼接保通报列非空
                "facts_note": payload.get("facts_note") or "；".join(payload.get("reasons") or []),
                "ts": line.get("ts", ""),
                "ts_short": local_ts.strftime("%m-%d %H:%M"),
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
        "meta": parse_advice_card(md, stem=path.stem),
        # rendered：呈现态原值（12-orchestration §⑫；落盘 md 维持脱敏零改动）
        "rendered": _advice_rendered(request, code),
        # reviewed：已复核状态（advice_reviewed 决策反查；0.0.25）
        "reviewed": has_review(decisions(request, tail=200), date=date, code=code),
        # price：呈现态三源现价（signals 同源单股取值；无价 None——价格非敏感量且系呈现态字段）
        "price": _advice_price(request, code),
    }


def _advice_price(request: Request, code: str) -> dict | None:
    """⑤卡详情现价：signals 端点同源三源合并单股取值（零网络，只读台账）。"""
    row = next((r for r in signals(request).get("rows", []) if r.get("code") == code), None)
    if not row or row.get("price") is None:
        return None
    return {
        "price": row["price"],
        "asof": str(row.get("price_asof") or ""),
        "source": str(row.get("price_source") or ""),
        "stale": bool(row.get("price_stale")),
    }


@router.get("/advice/export/{date}/{code}")
def advice_export(date: str, code: str, request: Request) -> PlainTextResponse:
    """导出脱敏卡片：已落盘卡原样下发（NR-5 脱敏在写侧已完成，零改写零重算）。

    路径三保险（沿 0.0.21 audit 先例）：日期正则 + code 正则 + resolve 边界。
    """
    path = _guarded_file(
        _layout(request).advice_dir,
        (date, f"{code}.md"),
        part_res=(r"\d{4}-\d{2}-\d{2}", r"\d{5,6}\.md"),
        bad="建议卡路径非法",
        missing=f"建议卡不存在：{date}/{code}",
    )
    md = path.read_text(encoding="utf-8")
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="advice-{date}-{code}.md"'},
    )


def _advice_rendered(request: Request, code: str) -> dict | None:
    """运行时现算份额原值：sim-portfolio+tplus1（无持仓→None；落盘与负断言零改动）。"""

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
    except DecisionChainError as exc:
        return None, 503, str(exc)
    return asdict(task), 201, ""


@router.post("/queue/submit", status_code=201)
def submit(payload: SubmitRequest, request: Request) -> dict:
    task, status, detail = _do_submit(request, payload.kind, payload.code)
    if task is None:
        raise HTTPException(status_code=status, detail=detail)
    return task


@router.post("/panel/submit")
def panel_submit(
    request: Request, kind: str = Form(...), code: str = Form(...), ctx: str = Form("dg")
) -> HTMLResponse:
    """ctx=mo（③页复核发起）：任务卡不持 #dg-session hook，改给「在②页观测会话」跳转。"""
    try:
        payload = SubmitRequest(kind=kind, code=code)  # 同一白名单闸（kind/code 校验不因表单放宽）
    except ValidationError as exc:
        return HTMLResponse(
            render_submit_result(None, status=422, detail=str(exc.errors(include_url=False))),
            status_code=422,
        )
    task, status, detail = _do_submit(request, payload.kind, payload.code)
    session_target = "#dg-session" if ctx != "mo" else None
    return HTMLResponse(
        render_submit_result(task, status=status, detail=detail, session_target=session_target),
        status_code=status,
    )


# --- HTML fragment（旧版 overview 已被 §⑫ 新七页端点族取代）


@router.get("/panel/events")
def panel_events(request: Request) -> HTMLResponse:
    """③事件表 fragment：注入名称链与当日建议卡 stem 集（详情按钮联动的数据前置）。"""

    payload = _monitor_events_payload(request, date=None, tail=50)
    payload["names"] = load_name_map(_layout(request))
    advice_dir = _layout(request).advice_dir / str(payload.get("date") or "")
    payload["advice_codes"] = (
        sorted(p.stem for p in advice_dir.glob("*.md")) if advice_dir.exists() else []
    )
    return HTMLResponse(render_events_table(payload))


@router.get("/panel/audit")
def panel_audit(request: Request) -> HTMLResponse:
    """⑦审计：strict 载入 + 断链降级（原始行渲染 + 警示；修复走 repair-decisions CLI）。"""
    import json as _json
    from dataclasses import asdict

    layout = _layout(request)
    try:
        log = DecisionLog.load(layout.decisions_path)
        total_lines = len(log.lines)
        lines = [asdict(line) for line in log.lines][-20:]
        chain_ok: bool | None = log.verify_chain()
    except DecisionChainError:
        rows: list[dict] = []
        if layout.decisions_path.exists():
            for raw in layout.decisions_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    rows.append(_json.loads(raw))
                except ValueError:
                    continue  # 坏行跳过——降级投影宁可少列不可拒渲
        lines = rows[-20:]
        total_lines = len(rows)
        chain_ok = False
    return HTMLResponse(
        render_audit(lines, budget(request), chain_ok=chain_ok, decisions_total=total_lines)
    )


# --- audit/ 归档只读（三件套互查第三腿，0.0.21；读写矩阵 §④ 面板读半边闭合） ---

_AUDIT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AUDIT_FILE_RE = re.compile(r"^[A-Za-z0-9:_-]+\.jsonl$")


def _audit_dir_of(request: Request, date: str) -> Path | None:
    """归档目录解析（三保险前两道：日期/文件名正则；第三道 resolve 边界在调用方）。"""
    if not _AUDIT_DATE_RE.match(date):
        return None
    root = _layout(request).audit_dir / date
    return root if root.exists() else None


@router.get("/audit/dates")
def audit_dates(request: Request) -> dict:
    root = _layout(request).audit_dir
    dates = (
        sorted(
            (d.name for d in root.iterdir() if d.is_dir() and _AUDIT_DATE_RE.match(d.name)),
            reverse=True,
        )
        if root.exists()
        else []
    )
    return {"dates": dates}


@router.get("/audit/{date}")
def audit_index(date: str, request: Request) -> dict:
    day_dir = _audit_dir_of(request, date)
    if day_dir is None:
        raise HTTPException(status_code=404, detail=f"无归档日期：{date}")
    files = []
    for f in sorted(day_dir.glob("*.jsonl")):
        if not _AUDIT_FILE_RE.match(f.name):
            continue
        stat = f.stat()
        files.append(
            {
                "file": f.name,
                "size_bytes": stat.st_size,
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.UTC).isoformat(
                    timespec="seconds"
                ),
            }
        )

    return {"date": date, "count": len(files), "files": files}


@router.get("/audit/{date}/{name}")
def audit_read(
    date: str, name: str, request: Request, tail: int = Query(default=100, ge=1, le=500)
) -> dict:
    """转录 tail 读取（三保险：日期/文件名正则 + resolve 边界；行级原文，本地只读）。"""
    day_dir = _audit_dir_of(request, date)
    if day_dir is None:
        raise HTTPException(status_code=404, detail="归档不存在")
    path = _guarded_file(
        day_dir,
        (name,),
        part_res=(r"[A-Za-z0-9:_\-]+\.jsonl",),
        bad="归档不存在",
        missing="归档不存在",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        "date": date,
        "file": name,
        "total_lines": len(lines),
        "lines": lines[-tail:],
    }


@router.get("/panel/audit-archive")
def panel_audit_archive(request: Request, date: str | None = None) -> HTMLResponse:
    """⑦归档索引卡（缺省最新有归档日期；行级查看按钮填 #au-transcript）。"""

    dates = audit_dates(request)["dates"]
    day = date if date and _AUDIT_DATE_RE.match(date) else (dates[0] if dates else "")
    payload = audit_index(day, request) if day else {"date": "", "count": 0, "files": []}
    payload["dates"] = dates
    return HTMLResponse(render_audit_archive(payload))


@router.get("/panel/audit-archive/{date}/{name}")
def panel_audit_read(date: str, name: str, request: Request) -> HTMLResponse:
    """转录 tail fragment（<pre> 原文；行数夹紧 100）。"""

    body = audit_read(date, name, request, tail=100)
    return HTMLResponse(render_audit_transcript(body))


@router.get("/panel/sessions")
def panel_sessions(request: Request) -> HTMLResponse:
    """⑦会话登记表（审计三件套 sessions 腿呈现，0.0.16）。"""

    return HTMLResponse(render_sessions_table(sessions(request)))


@router.get("/panel/task/{task_id}")
def panel_task(task_id: str, request: Request) -> HTMLResponse:
    q = queue(request)
    task = next((t for t in q.get("tasks", []) if t.get("task_id") == task_id), None)
    if task is None:
        return HTMLResponse(
            '<div class="task-card" id="task-card"><p class="warn">任务不存在</p></div>'
        )
    return HTMLResponse(render_task_card(task))
