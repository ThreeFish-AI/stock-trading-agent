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
