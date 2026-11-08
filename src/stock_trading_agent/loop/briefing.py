"""晨报/盘后复盘行动项卡（M2：面板手动触发；自动化属 M3）。

落位 agent/briefing/<date>/<code>.md（每日每股一卡，同日重跑覆盖——早晚分卡留 M3）；
行动项措辞限定「复核」禁指令词（R7 同源）；脱敏三层（prompt 禁读模拟台账 + 断言器键扫 +
卡面负断言：禁数量词）；ack 走 decision log（kind=briefing_ack，hash 链防篡改免费）。
"""

from __future__ import annotations

import re
from pathlib import Path

from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.store import atomic_write_text

BRIEFING_KIND = "briefing_review"
_QUANTITY_RE = re.compile(r"\d{1,7}(?:\.\d+)?\s*(?:股|份|手|lots)")
DISCLAIMER_FOOTER = (
    "本卡由 Stock Trading Agent 生成，仅供学习与研究参考，不构成投资建议；"
    "AI 生成，用户复核前置；行动项均为「复核」性质，不含买卖指令。"
)


def build_card_md(structured: dict, *, date: str, code: str) -> str:
    """briefing structured_output → 卡面 md（行动项区措辞限定「复核」；零数量词）。"""
    briefing_text = str(structured.get("briefing", "")).strip() or "（无正文）"
    action_items = structured.get("action_items")
    lines = [
        "---",
        "card: briefing/1",
        f"code: {code}",
        f"date: {date}",
        "written_by: stock-trading-agent",
        "---",
        "",
        f"## 盘前晨报 / 盘后复盘 · {code} · {date}",
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


def persist_card(layout: StateLayout, date: str, code: str, md: str) -> Path:
    """断言器放行后原子写（同日重跑覆盖）。"""
    path = layout.briefing_card_path(date, code)
    atomic_write_text(path, md)
    return path


def has_ack(decision_lines: list[dict], *, date: str, code: str) -> bool:
    """确认状态走 decision log（kind=briefing_ack payload 命中 date+code）。"""
    for line in decision_lines:
        if line.get("kind") != "briefing_ack":
            continue
        payload = line.get("payload") or {}
        if payload.get("date") == date and payload.get("code") == code:
            return True
    return False
