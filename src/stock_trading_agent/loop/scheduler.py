"""定时器占位（stub）：next_due 按市场独立计时模型；launchd 槽完成事件经 watchfiles 监听台账。

M1 接线期落地（方案 §3「T1 自动刷新接线（零 LLM，消费 monitor 台账）」）；本骨架无线程无调度行为。
坑位（I-52）：launchd WatchPaths 不用（race-prone）；交易时段 caffeinate -i 防睡死（I-50）。
"""

from __future__ import annotations

from dataclasses import dataclass

MARKET_SESSIONS = {
    "A": {"open": "09:30", "close": "15:00"},
    "HK": {"open": "09:30", "close": "16:00"},
}


@dataclass(frozen=True)
class MarketClock:
    """按市场独立的时钟模型（沿 vibe-stock watch.py 先例：next_due 按各自市场计）。"""

    market: str

    def session_bounds(self) -> tuple[str, str]:
        sess = MARKET_SESSIONS.get(self.market, MARKET_SESSIONS["A"])
        return sess["open"], sess["close"]


def next_due_hint(market: str) -> tuple[str, str]:
    """占位：返回市场开收盘界（真实 next_due 计算随 T1 接线落地）。"""
    return MarketClock(market).session_bounds()
