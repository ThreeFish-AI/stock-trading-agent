"""SSE 端点：GET /api/stream（单流多命名事件 + 15s ping + 30s status 快照节拍）。

E-裁3：面板 compound trigger `every 180s, sse:X`（逗号=OR）——SSE 挂了轮询仍在（I-23 兜底）。
E-裁7 坑位：vibe-stock 两锁 flock 获取/释放不改 mtime（锁文件不删除）——锁态不可文件监听，
只能周期快照 diff 推 status 事件（30s 节拍比 180s HTTP 轮询密、不增请求数）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from stock_trading_agent.loop.events import lock_is_held
from stock_trading_agent.state.paths import StateLayout

router = APIRouter(prefix="/api")

PING_S = 15  # sse-starlette 默认保活（I-27）
STATUS_TICK_S = 30  # 锁态/三槽快照节拍（flock 不可文件监听坑位）

# R1''：建连初始快照——首屏与断线重连不待 180s 轮询/下一变更（多命名事件全集一发）
INITIAL_SNAPSHOT_EVENTS = frozenset(
    {
        "queue",
        "decisions",
        "budget",
        "briefing",
        "monitor",
        "advice",
        "status",
        "outlook",
        "paper",
        "signals",
        "reports",
    }
)


def _status_fingerprint(layout: StateLayout) -> str:
    watch_state = layout.monitor_state_path
    try:
        mtime = watch_state.stat().st_mtime if watch_state.exists() else 0.0
    except OSError:
        mtime = 0.0
    return f"{lock_is_held(layout.watch_lock)}|{lock_is_held(layout.fetch_lock)}|{mtime}"


@router.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    hub = request.app.state.stream_hub
    layout: StateLayout = request.app.state.sse_layout

    async def event_gen():
        q = hub.subscribe()
        last_status = _status_fingerprint(layout)
        try:
            yield {"event": "retry", "data": "3000"}
            # R1'' 建连即推初始快照（multi 一发驱动全部 sse:multi fragment 立即 GET）
            yield {"event": "multi", "data": ",".join(sorted(INITIAL_SNAPSHOT_EVENTS))}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    events = await asyncio.wait_for(q.get(), timeout=STATUS_TICK_S)
                    yield (
                        {"event": sorted(events)[0], "data": "changed"}
                        if len(events) == 1
                        else {
                            "event": "multi",
                            "data": ",".join(sorted(events)),
                        }
                    )
                except TimeoutError:
                    pass
                fingerprint = _status_fingerprint(layout)
                if fingerprint != last_status:  # 锁态/三槽快照 diff（30s 节拍）
                    last_status = fingerprint
                    yield {"event": "status", "data": fingerprint}
        finally:
            hub.unsubscribe(q)

    return EventSourceResponse(event_gen(), ping=PING_S)
