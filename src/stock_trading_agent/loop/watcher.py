"""monitor 台账监听：watchfiles 线程盯 events 目录，偏移断点消费增量（方案 §10①、I-45/I-52）。

纪律：launchd WatchPaths 不用（race-prone）——进程内 watchfiles；watchfiles 事件只当触发器，
真实增量以文件偏移为准（防丢防重）；偏移断点持久化 agent/watcher.json（崩溃不重放已登记事件，
日切换清键）。写者恒为 vibe-stock watch.py，本层零写入（R7/R8）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from stock_trading_agent.loop.monitor_events import MonitorEvent, market_today, read_new_lines
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.store import atomic_write_json, read_json

POLL_FALLBACK_S = 5.0  # events 目录不存在时的等待步长
WATCH_RUST_TIMEOUT_MS = 2000  # watchfiles 中断重查步长（事件即时性 ≤2s）


class EventsWatcher:
    """monitor/events/<date>.jsonl 增量监听（回调式；poll_once 可同步直调供测试）。"""

    def __init__(
        self,
        *,
        layout: StateLayout,
        on_events: Callable[[list[MonitorEvent]], None],
    ) -> None:
        self.layout = layout
        self._on_events = on_events
        self._offsets: dict[str, int] = {}
        self._date = ""
        self._last_saved: dict | None = None  # W6：偏移零变化跳过重写（消 ~4.3 万次/日无效 fsync）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_state()

    # --- 偏移断点持久化（agent/watcher.json） ---
    def _load_state(self) -> None:
        data = read_json(self.layout.watcher_state_path)
        if data and data.get("date") == market_today():
            self._date = str(data["date"])
            self._offsets = {str(k): int(v) for k, v in (data.get("offsets") or {}).items()}
            self._last_saved = data  # 盘上即此态：下次相同不重写

    def _save_state(self) -> None:
        # 坑位：offsets 须浅拷贝快照——直嵌 self._offsets 引用会与 _last_saved 别名同一 dict，
        # read_new_lines 原地推进后值比较恒等，偏移自此永不落盘（重启即重放当日事件）。
        payload = {"version": 1, "date": self._date, "offsets": dict(self._offsets)}
        if payload == self._last_saved:
            return  # W6：偏移与日历零变化——不重写（SSE 桥靠 mtime diff，跳过不产生漏事件）
        atomic_write_json(self.layout.watcher_state_path, payload)
        self._last_saved = payload

    # --- 增量消费（同步可测） ---
    def poll_once(self) -> list[MonitorEvent]:
        today = market_today()
        if today != self._date:  # 日切换：新文件从 0 起，旧键整体作废
            self._date = today
            self._offsets = {}
        events = read_new_lines(self.layout.monitor_events_file(today), self._offsets)
        self._save_state()
        if events:
            self._on_events(events)
        return events

    # --- 线程生命周期 ---
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="sta-events-watcher", daemon=True)
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def run(self) -> None:
        """线程体：启动即 poll 一次（补离线窗），随后 watchfiles 变更/中断节拍持续增量。"""
        self.poll_once()
        while not self._stop.is_set():
            if not self.layout.monitor_events_dir.exists():
                self._stop.wait(POLL_FALLBACK_S)
                self.poll_once()
                continue
            try:
                import watchfiles

                for _changes in watchfiles.watch(
                    self.layout.monitor_events_dir,
                    stop_event=self._stop,
                    rust_timeout=WATCH_RUST_TIMEOUT_MS,
                    yield_interrupted=True,
                ):
                    if self._stop.is_set():
                        break
                    self.poll_once()
            except Exception:  # watchfiles 不可用/目录消失：退化为步长轮询
                self._stop.wait(POLL_FALLBACK_S)
                self.poll_once()
