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
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from stock_trading_agent import __version__
from stock_trading_agent.loop.advice import parse_advice_card
from stock_trading_agent.loop.events import lock_is_held
from stock_trading_agent.loop.loop import LoopReject
from stock_trading_agent.loop.monitor_events import market_today, parse_line
from stock_trading_agent.server.panel import (
    render_audit,
    render_events_table,
    render_overview,
    render_submit_result,
    render_task_card,
)
from stock_trading_agent.server.schemas import REJECT_STATUS, SubmitRequest
from stock_trading_agent.state.paths import StateLayout

router = APIRouter(prefix="/api")

_REPORTS_DIR_RE = re.compile(r"^\d{5,6}\.(cn|hk)$")


def _layout(request: Request) -> StateLayout:
    root = getattr(request.app.state, "state_root", None)
    if root is not None:
        return StateLayout(root=Path(root))
    return StateLayout.from_env()


def _runtime(request: Request):
    return getattr(request.app.state, "loop_runtime", None)


# --- 只读态（§11 面板读半边） ---


@router.get("/health")
def health() -> dict:
    return {"ok": True, "component": "stock-trading-agent-panel", "version": __version__}


@router.get("/queue")
def queue(request: Request) -> dict:
    from stock_trading_agent.state.queue import QueueState
    from stock_trading_agent.state.store import read_json

    data = read_json(_layout(request).queue_path)
    return data if data is not None else QueueState().to_dict()


@router.get("/sessions")
def sessions(request: Request) -> dict:
    from stock_trading_agent.state.sessions import SessionsState
    from stock_trading_agent.state.store import read_json

    data = read_json(_layout(request).sessions_path)
    return data if data is not None else SessionsState().to_dict()


@router.get("/decisions")
def decisions(request: Request, tail: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    path = _layout(request).decisions_path
    if not path.exists():
        return []
    lines = [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw]
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
    from stock_trading_agent.state.store import read_json

    data = read_json(_layout(request).budget_path)
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
    """核心载荷（HTTP 端点与 fragment 复用；tail 夹紧 1..500）。"""
    tail = max(1, min(int(tail), 500))
    day = date or market_today()
    path = _layout(request).monitor_events_file(day)
    events: list[dict] = []
    skipped = 0
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
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
                    "review": ev.review,
                    "suppress_reason": ev.suppress_reason,
                }
            )
    return {"date": day, "count": len(events), "skipped": skipped, "events": events[-tail:]}


@router.get("/monitor/status")
def monitor_status(request: Request) -> dict:
    from stock_trading_agent.state.store import read_json

    layout = _layout(request)
    day = market_today()
    watch_state = read_json(layout.monitor_state_path) or {}
    runtime = _runtime(request)

    queued = running = 0
    q = read_json(layout.queue_path) or {"tasks": []}
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


# --- 后验战绩⑥ + 晨报卡（M2） ---


@router.get("/stats")
def stats(request: Request) -> dict:
    from stock_trading_agent.server.stats import aggregate_decisions, budget_curve

    return {
        "decisions": aggregate_decisions(decisions(request, tail=200)),
        "budget_curve": budget_curve(list(sessions(request).get("sessions", {}).values())),
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
    from dataclasses import asdict

    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.server.panel import render_briefing_fragment
    from stock_trading_agent.state.store import atomic_append_jsonl

    runtime = _runtime(request)
    if runtime is None or not runtime.is_active:
        raise HTTPException(status_code=503, detail="运行时未持锁（briefing_ack 需 daemon 持有者）")
    layout = _layout(request)
    if date != market_today():
        raise HTTPException(status_code=422, detail="仅当日卡可确认")
    line = runtime.loop.decisions.append(_now_iso(), "briefing_ack", {"date": date, "code": code})
    atomic_append_jsonl(layout.decisions_path, asdict(line))
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
    return {
        "exists": portfolio is not None,
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
            from dataclasses import asdict

            from stock_trading_agent.state.store import atomic_append_jsonl

            if runtime.loop.decisions.lines:
                atomic_append_jsonl(layout.decisions_path, asdict(runtime.loop.decisions.lines[-1]))
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
    return {"code": code, "date": date, "md": md, "meta": parse_advice_card(md)}


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


# --- HTML fragment（htmx 消费；180s 常态轮询、任务卡 5s） ---


@router.get("/panel/overview")
def panel_overview(request: Request) -> HTMLResponse:
    events = _monitor_events_payload(request, date=None, tail=50)
    return HTMLResponse(
        render_overview(monitor_status(request), events, queue(request), budget(request))
    )


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
