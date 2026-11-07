"""只读态路由：/api/health|queue|sessions|decisions|paper|budget（方案 §11 面板读半边）。

写动词（POST /api/queue/submit）= 501 显式占位（提交半边属 M1 接线期）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from stock_trading_agent.state.paths import StateLayout

router = APIRouter(prefix="/api")


def _layout(request: Request) -> StateLayout:
    root = getattr(request.app.state, "state_root", None)
    if root is not None:
        return StateLayout(root=Path(root))
    return StateLayout.from_env()


@router.get("/health")
def health() -> dict:
    return {"ok": True, "component": "stock-trading-agent-panel", "version": "0.0.1"}


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
def decisions(request: Request, tail: int = 20) -> list[dict]:
    import json

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
            "note": "骨架期：预算台账未初始化（默认关，仅记账）",
        }
    )


@router.post("/queue/submit")
def submit(_payload: dict) -> Response:
    raise HTTPException(
        status_code=501, detail="提交通道属 M1 接线期（§11 面板『读+提交命令』的提交半边）"
    )
