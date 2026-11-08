"""泛型多目录监听（SSE 事件桥）：mtime 快照 diff → 命名事件（I-45：与写进程零耦合）。

与 EventsWatcher 分立（E-裁1）：EventsWatcher 承载 T1 的偏移断点与 MonitorEvent 语义
（领域态）；本桥只做「目录变了 → 通知重 GET fragment」，无需偏移。坑位（flock 锁态
不可文件监听）：锁持有/释放不改 mtime——锁态走 SSE 侧 30s 节拍快照推流（sse.py）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

POLL_FALLBACK_S = 5.0
WATCH_RUST_TIMEOUT_MS = 2000


class DirWatcher:
    """roots 多目录 mtime 监听：变更文件集经 to_event 归并为命名事件集。"""

    def __init__(
        self,
        *,
        roots: tuple[Path, ...],
        on_change: Callable[[set[str]], None],
        to_event: Callable[[Path], str | None],
    ) -> None:
        self.roots = tuple(roots)
        self._on_change = on_change
        self._to_event = to_event
        self._snapshot: dict[Path, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> set[str]:
        """mtime 快照 diff（同步可测口）：返回变更引发的事件名集。"""
        current: dict[Path, float] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    try:
                        current[path] = path.stat().st_mtime
                    except OSError:
                        continue
        changed = {path for path, mtime in current.items() if self._snapshot.get(path) != mtime} | {
            path for path in self._snapshot if path not in current and path.exists() is False
        }
        self._snapshot = current
        events = {name for name in (self._to_event(p) for p in changed) if name}
        return events

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="sta-dirwatch", daemon=True)
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def run(self) -> None:
        self.poll_once()  # 启动建基线（首扫不触发事件——增量语义）
        while not self._stop.is_set():
            if not any(root.exists() for root in self.roots):
                self._stop.wait(POLL_FALLBACK_S)
                continue
            try:
                import watchfiles

                for _changes in watchfiles.watch(
                    *self.roots,
                    stop_event=self._stop,
                    rust_timeout=WATCH_RUST_TIMEOUT_MS,
                    yield_interrupted=True,
                ):
                    if self._stop.is_set():
                        break
                    events = self.poll_once()
                    if events:
                        self._on_change(events)
            except Exception:  # watchfiles 不可用/目录消失：退化为步长轮询
                self._stop.wait(POLL_FALLBACK_S)
                events = self.poll_once()
                if events:
                    self._on_change(events)
