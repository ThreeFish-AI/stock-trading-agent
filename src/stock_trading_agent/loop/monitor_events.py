"""monitor 台账事件解析与 T1 触发判据（纯函数，零 IO；方案 §10①②、07 事件梯）。

消费对象：vibe-stock watch.py 的 append-only 台账 `monitor/events/<date>.jsonl`，
行 schema（07/0.0.25 实测）：{ts, code, market, kind, rule, level("T0"), notify,
mech_risk(null|{downgrade,severity}), facts{...}, review?("××复核"), suppress_reason?}。
写者恒为 watch.py（R7/R8），本层只读解析。
坑位：append-only 非原子可见——尾行可能截断，坏行返回 None 不抛（read_new_lines 半行缓存）。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from stock_trading_agent.loop.events import MARKET_TZ

# T1 触发的 mech_risk 降档事实档位（07：mech_risk 降档事实属机械判据族）
T1_MECH_RISK_SEVERITIES: frozenset[str] = frozenset({"high", "medium"})


@dataclass(frozen=True)
class MonitorEvent:
    ts: str
    code: str  # 股票码；市场级事件为 "*"
    market: str  # "cn" | "hk"
    kind: str  # universe 行 kind（real|sim|watch|close|*）
    rule: str
    level: str  # 现行恒 "T0"（07 事件梯：T1/T2 为预留）
    notify: bool
    mech_risk: dict | None = None
    facts: dict = field(default_factory=dict)
    review: str | None = None  # 「××复核」短语（_REVIEW_HINT 登记规则才有）
    suppress_reason: str | None = None


def parse_line(raw: str) -> MonitorEvent | None:
    """解析一行台账；坏行/空行/缺必需字段返回 None（不抛——尾行截断是常态）。"""
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return MonitorEvent(
            ts=str(data["ts"]),
            code=str(data["code"]),
            market=str(data["market"]),
            kind=str(data.get("kind", "*")),
            rule=str(data.get("rule", "")),
            level=str(data.get("level", "T0")),
            notify=bool(data.get("notify", False)),
            mech_risk=data.get("mech_risk"),
            facts=dict(data.get("facts") or {}),
            review=(str(data["review"]) if data.get("review") else None),
            suppress_reason=(str(data["suppress_reason"]) if data.get("suppress_reason") else None),
        )
    except KeyError:
        return None


def t1_trigger(ev: MonitorEvent) -> bool:
    """T1 触发判据（四条件 AND；宁漏勿扰，判据只收严向，12-orchestration §③）。

    level=="T0" ∧ code≠"*"（个股事件）∧ 未被压制
    ∧（review 复核短语非空 ∨ mech_risk.severity∈{high,medium}）。
    """
    if ev.level != "T0" or ev.code == "*" or ev.suppress_reason is not None:
        return False
    if (ev.review or "").strip():
        return True
    severity = (ev.mech_risk or {}).get("severity")
    return severity in T1_MECH_RISK_SEVERITIES


def market_today(now: _dt.datetime | None = None) -> str:
    """市场本地钟面（Asia/Shanghai）日期——events 文件名 <date>.jsonl 的 date 键。"""
    if now is None:
        now = _dt.datetime.now(tz=_dt.UTC)
    return now.astimezone(MARKET_TZ).strftime("%Y-%m-%d")


def read_new_lines(path: Path, offsets: dict[str, int]) -> list[MonitorEvent]:
    """按文件偏移增量读取并解析；只消费到最后一个换行（半行留待下次）。

    offsets 按文件名键控（日切换互不干扰）；调用方负责持久化 offsets。
    """
    name = path.name
    offset = offsets.get(name, 0)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    if size <= offset:
        return []
    with path.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read(size - offset)
    last_newline = blob.rfind(b"\n")
    if last_newline < 0:
        return []  # 全是半行，等下次
    offsets[name] = offset + last_newline + 1
    events: list[MonitorEvent] = []
    for raw in blob[: last_newline + 1].decode("utf-8", errors="replace").splitlines():
        ev = parse_line(raw)
        if ev is not None:
            events.append(ev)
    return events
