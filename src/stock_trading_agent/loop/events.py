"""① 触发源：事件信封 + dedup_key 幂等键 + 17:30–18:10 禁跑窗 + 锁前置检查（方案 §10①）。

三类触发源：panel（面板手动）/ launchd（槽完成事件）/ monitor（M3 盯盘自动接管，默认关）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

SOURCES = ("panel", "launchd", "monitor")

# 17:30–18:10 禁跑 report（vibe-stock 收盘评估窗 references/05 I-005；锁纪律全继承，方案 §14）。
# 窗口为市场本地钟面（launchd 17:35/18:05 槽位均 Asia/Shanghai），判定前须换算。
MARKET_TZ = ZoneInfo("Asia/Shanghai")
NO_REPORT_WINDOW = ((17, 30), (18, 10))

# 禁跑窗覆盖的 kind：写 signals.json 的全诊/批量类 + report 前缀 + 晨报复盘（收严向）
REPORT_BLOCKING_KINDS = frozenset(
    {"diagnose_full", "diagnose_quick", "batch_wave", "briefing_review"}
)


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    source: str  # panel | launchd | monitor
    kind: str  # 路由 kind（diagnose_full / monitor_review / …）
    code: str = ""  # 标的码（市场级事件为空）
    payload: dict | None = None
    ts: str = ""


def dedup_key(event: EventEnvelope, date: str) -> str:
    """幂等键 f"{kind}:{code}:{date}"（§10①；queue.submit 按此拒重）。"""
    return f"{event.kind}:{event.code}:{date}"


def _minutes(time: _dt.time) -> int:
    return time.hour * 60 + time.minute


def in_no_report_window(ts: _dt.datetime) -> bool:
    """禁跑窗判定（按市场本地钟面 Asia/Shanghai 换算；坑位：传 UTC 会错位 8 小时）。"""
    (start_h, start_m), (end_h, end_m) = NO_REPORT_WINDOW
    local = ts.astimezone(MARKET_TZ)
    minutes = _minutes(local.time())
    return _minutes(_dt.time(start_h, start_m)) <= minutes <= _minutes(_dt.time(end_h, end_m))


def gate_report_event(ts: _dt.datetime, kind: str) -> str | None:
    """禁跑窗闸：report 类 kind 窗内返回 defer 理由，窗外/非 report 返回 None（放行）。"""
    if (kind in REPORT_BLOCKING_KINDS or kind.startswith("report")) and in_no_report_window(ts):
        return (
            "17:30–18:10 禁跑窗内拒跑 report（收盘评估窗，§14 锁纪律；窗口按 Asia/Shanghai 钟面）"
        )
    return None


def lock_precheck(fetch_lock: bool, watch_lock: bool) -> str | None:
    """锁前置检查（弱尊重：vibe-stock fetch_state.lock / watch.lock 存在即 defer）。

    坑位：两锁的 flock 实现在 vibe-stock watch.py（129/159 行区），本层只做存在性检查；
    M1 接线期须对齐同一 flock 语义后再升强互锁。
    """
    if fetch_lock:
        return "fetch_state.lock 在场：取数串行中，任务 defer"
    if watch_lock:
        return "watch.lock 在场：盯盘中，任务 defer"
    return None
