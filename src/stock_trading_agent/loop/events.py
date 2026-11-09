"""① 触发源：事件信封 + dedup_key 幂等键 + 17:30–18:10 禁跑窗 + 锁前置检查（方案 §10①）。

三类触发路径：panel（面板手动）/ launchd（槽台账经 watchfiles 监听，入队 source=monitor）/
monitor（盯盘事件自动触发：auto-T2/T1/早晚 briefing，0.0.9 起非 M3 预留）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

# 17:30–18:10 禁跑 report（vibe-stock 收盘评估窗 references/05 I-005；锁纪律全继承，方案 §14）。
# 窗口为市场本地钟面（launchd 17:35/18:05 槽位均 Asia/Shanghai），判定前须换算。
MARKET_TZ = ZoneInfo("Asia/Shanghai")
NO_REPORT_WINDOW = ((17, 30), (18, 10))

# 禁跑窗覆盖的 kind：写 signals.json 的全诊/批量类 + report 前缀 + 晨报复盘（收严向）
REPORT_BLOCKING_KINDS = frozenset(
    {
        "diagnose_full",
        "diagnose_quick",
        "batch_wave",
        "briefing_review",
        "briefing_morning",
        "briefing_evening",
    }
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


def lock_is_held(path) -> bool:
    """flock 持有态探测（对齐 vibe-stock advisory 锁语义；判持有不判存在）。

    做法：文件缺席=无人持有；在场则尝试 LOCK_EX|LOCK_NB——立即取到=无人持有（随即释放，
    持有窗口微秒级），EWOULDBLOCK=持有中。
    坑位：vibe-stock 两锁的锁文件不删除（防删除-重建竞态），存在性检查会把陈旧锁文件
    误判为持有中（本机实测：非交易日残留 watch.lock 导致全部 defer 拒提交）。
    """
    import fcntl

    if not path.exists():
        return False
    try:
        fh = path.open("r+")
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    fh.close()
    return False


def lock_precheck(fetch_lock: bool, watch_lock: bool) -> str | None:
    """锁前置检查（尊重 vibe-stock flock 语义：持有中才 defer，调用方传 lock_is_held() 结果）。"""
    if fetch_lock:
        return "fetch_state.lock 持有中：取数串行进行，任务 defer"
    if watch_lock:
        return "watch.lock 持有中：盯盘进行，任务 defer"
    return None
