"""晨报/盘后复盘行动项卡（0.0.18 早晚分卡六层加槽；自动触发与分卡同批落地）。

落位 agent/briefing/<date>/<code>.<am|pm>.md（每日每股每槽一卡，同槽重跑覆盖；
legacy briefing_review 仍落 <code>.md 兼容旧卡）；行动项措辞限定「复核」禁指令词
（R7 同源）；脱敏三层（prompt 禁读模拟台账 + 断言器键扫 + 卡面负断言：禁数量词）；
ack 走 decision log（kind=briefing_ack payload 带 session 槽位，hash 链防篡改）。
自动触发（plan_auto_briefings 纯函数 + worker 钩子）：盘前 09:00–09:29 / 盘后 18:15+，
universe 全码、L2 总闸开启才入队（无人值守语义与 §⑩ 同源）、每股每槽每日一次幂等。
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.store import atomic_write_text

BRIEFING_KIND = "briefing_review"  # legacy（旧队列任务零迁移，仍可路由）
BRIEFING_MORNING = "briefing_morning"
BRIEFING_EVENING = "briefing_evening"
BRIEFING_KINDS = frozenset({BRIEFING_KIND, BRIEFING_MORNING, BRIEFING_EVENING})

SLOT_LABELS = {"am": "盘前晨报", "pm": "盘后复盘", "": "盘前晨报 / 盘后复盘"}

# 自动触发窗（市场钟面 Asia/Shanghai；工作日判定；盘前窗在开盘 09:30 前、
# 盘后窗在禁跑窗 18:10 之后留 5 分钟余量）
AUTO_MORNING_WINDOW = ((9, 0), (9, 29))
AUTO_EVENING_WINDOW = ((18, 15), (23, 59))

_QUANTITY_RE = re.compile(r"\d{1,7}(?:\.\d+)?\s*(?:股|份|手|lots)")
DISCLAIMER_FOOTER = (
    "本卡由 Stock Trading Agent 生成，仅供学习与研究参考，不构成投资建议；"
    "AI 生成，用户复核前置；行动项均为「复核」性质，不含买卖指令。"
)


def slot_of_kind(kind: str) -> str:
    """kind → 槽位（am/pm/""=legacy 通用卡）。"""
    return {BRIEFING_MORNING: "am", BRIEFING_EVENING: "pm"}.get(str(kind), "")


def build_card_md(structured: dict, *, date: str, code: str, slot: str = "") -> str:
    """briefing structured_output → 卡面 md（行动项区措辞限定「复核」；零数量词）。"""
    briefing_text = str(structured.get("briefing", "")).strip() or "（无正文）"
    action_items = structured.get("action_items")
    head = ["---", "card: briefing/1", f"code: {code}", f"date: {date}"]
    if slot:
        head.append(f"session: {slot}")
    lines = [
        *head,
        "written_by: stock-trading-agent",
        "---",
        "",
        f"## {SLOT_LABELS.get(slot, '')} · {code} · {date}",
        "",
        briefing_text,
        "",
        "## 行动项（措辞限定「复核」；确认状态见面板）",
    ]
    if isinstance(action_items, list) and action_items:
        for item in action_items:
            lines.append(f"- [ ] 复核：{item!s}")
    else:
        lines.append("- [ ] （无待复核项）")
    lines += ["", "---", DISCLAIMER_FOOTER]
    return "\n".join(lines) + "\n"


def scan_briefing_text(md: str) -> list[str]:
    """卡面负断言（NR-5 第三层）：数量词/免责尾注缺失即拒（宁误拒不漏放）。"""
    reasons: list[str] = []
    if _QUANTITY_RE.search(md):
        reasons.append("晨报卡出现数量词（股|份|手|lots）——脱敏边界违例（NR-5）")
    if DISCLAIMER_FOOTER not in md:
        reasons.append("晨报卡缺免责尾注")
    for banned in ("买入", "卖出", "加仓", "减仓", "清仓"):
        if f"复核：{banned}" in md or f"- [ ] {banned}" in md:
            reasons.append(f"行动项出现指令词「{banned}」（R7：措辞限定「复核」）")
    return reasons


def persist_card(layout: StateLayout, date: str, code: str, md: str, *, slot: str = "") -> Path:
    """断言器放行后原子写（同槽同日重跑覆盖；legacy 无槽单卡）。"""
    path = layout.briefing_card_path(date, code, slot)
    atomic_write_text(path, md)
    return path


def has_ack(decision_lines: list[dict], *, date: str, code: str, slot: str = "") -> bool:
    """确认状态走 decision log（briefing_ack payload 命中 date+code+session）。

    槽位匹配：ack 带 session 精确匹配本槽卡；ack 无 session（legacy）只匹配 legacy 卡。
    """
    for line in decision_lines:
        if line.get("kind") != "briefing_ack":
            continue
        payload = line.get("payload") or {}
        if payload.get("date") != date or payload.get("code") != code:
            continue
        if (payload.get("session") or "") == slot:
            return True
    return False


def _in_window(local: _dt.datetime, window: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    (sh, sm), (eh, em) = window
    minutes = local.hour * 60 + local.minute
    return sh * 60 + sm <= minutes <= eh * 60 + em


def plan_auto_briefings(
    now_local: _dt.datetime,
    *,
    l2_enabled: bool,
    universe_codes: list[str] | None,
    already_queued: set[str],
) -> list[tuple[str, str]]:
    """自动触发判据纯函数（判据与 IO 分离；worker 钩子消费）。

    返回待入队 [(kind, code)]：工作日 ∧ L2 开 ∧ universe 可读 ∧ 槽窗内
    ∧ 该 code 当日该槽未入队（already_queued=当日已存在 dedup_key 集，含终态）。
    坑位：非工作日不触发（节假日未建模——多触发一次成本为一次小会话，宁多勿漏口径）。
    """
    if not l2_enabled or universe_codes is None:
        return []
    if now_local.weekday() >= 5:
        return []
    if _in_window(now_local, AUTO_MORNING_WINDOW):
        kind, date = BRIEFING_MORNING, now_local.strftime("%Y-%m-%d")
    elif _in_window(now_local, AUTO_EVENING_WINDOW):
        kind, date = BRIEFING_EVENING, now_local.strftime("%Y-%m-%d")
    else:
        return []
    return [
        (kind, code) for code in universe_codes if f"{kind}:{code}:{date}" not in already_queued
    ]
