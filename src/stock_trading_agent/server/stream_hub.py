"""SSE 广播桥：监听线程 → asyncio 订阅队列（loop.call_soon_threadsafe 线程安全发布）。

单流多命名事件（E-裁2）：queue/decisions/budget/monitor/advice/status/outlook/paper/briefing。
坑位：无订阅或 loop 已关时静默丢（桥线程永不因发布失败死）；发布带去抖合并（同拍事件并包）。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from stock_trading_agent.state.paths import StateLayout


def to_event_name(path: Path, layout: StateLayout) -> str | None:
    """文件路径 → 命名事件（面板 compound trigger 消费同名词）。"""
    try:
        relative = path.relative_to(layout.root)
    except ValueError:
        return None
    parts = relative.parts
    if parts[:1] == ("agent",):
        if parts[1:2] == ("queue.json",) or (len(parts) > 1 and parts[1] == "queue.json"):
            return "queue"
        if len(parts) > 1 and parts[1] == "decisions.jsonl":
            return "decisions"
        if len(parts) > 1 and parts[1] == "budget.json":
            return "budget"
        if len(parts) > 1 and parts[1] == "briefing":
            return "briefing"
        return None
    if parts[:1] == ("monitor",):
        if len(parts) > 1 and parts[1] == "events":
            return "monitor"
        if len(parts) > 1 and parts[1] == "advice":
            return "advice"
        return "status"  # state.json / day-summary / universe 等归状态面
    if parts[:1] == ("outlook",):
        return "outlook"
    if parts[:1] == ("sim-portfolio.json",) or parts[:1] == ("sim-pending-fills.json",):
        return "paper"
    if parts[:1] == ("signals.json",):
        return "signals"
    if parts[:1] == ("reports",):
        return "reports"
    return None


class StreamHub:
    """线程→asyncio 事件扇出：每个 SSE 订阅一条 asyncio.Queue。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish_threadsafe(self, events: set[str]) -> None:
        """桥线程发布入口：无 loop/无订阅静默丢（桥线程永不死）。"""
        with self._lock:
            loop = self._loop
            subscribers = list(self._subscribers)
        if loop is None or not subscribers or not events:
            return
        import contextlib

        with contextlib.suppress(RuntimeError):  # loop 已关（停机中）静默
            loop.call_soon_threadsafe(self._fanout, subscribers, frozenset(events))

    def _fanout(self, subscribers: list[asyncio.Queue], events: frozenset) -> None:
        import contextlib

        for q in subscribers:
            with contextlib.suppress(asyncio.QueueFull):  # 慢消费者丢拍（重 GET 幂等）
                q.put_nowait(events)
