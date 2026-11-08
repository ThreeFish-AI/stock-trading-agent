"""呈现层纯函数（零 IO 判据 + 薄读取器）：盘态/名称映射/信号快照/待办聚合/晨报行动项。

12-orchestration §⑫ 落点表。纪律：外仓 schema（signals.json 等）defensive 解析缺席降级
不炸页；root 直拼只在 server 层（state/ 禁字面量红线扫描纪律不动）。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from stock_trading_agent.loop.events import MARKET_TZ

VALID_SIGNALS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
MIN_SHOW_DAYS = 0  # 全部在册信号展示（无隐藏）


def load_root_json(path: Path) -> list | dict | None:
    """薄读取器：缺席/坏 JSON → None（调用方降级空态）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def market_phase(now: dt.datetime | None = None) -> dict:
    """盘态判定（连续窗简化：CN 09:30–15:00 / HK 09:30–16:00，午休并入盘中——登记升级位）。"""
    if now is None:
        now = dt.datetime.now(tz=dt.UTC)
    local = now.astimezone(MARKET_TZ)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()

    def phase_of(open_m: int, close_m: int) -> str:
        if weekday >= 5:
            return "closed"
        if minute < open_m:
            return "pre"
        if minute >= close_m:
            return "post"
        return "live"

    cn = phase_of(9 * 60 + 30, 15 * 60)
    hk = phase_of(9 * 60 + 30, 16 * 60)
    if "live" in (cn, hk):
        label = "盘中"
    elif cn == hk == "closed":
        label = "休市"
    elif "pre" in (cn, hk):
        label = "盘前"
    else:
        label = "盘后"
    overall = "live" if "live" in (cn, hk) else ("closed" if cn == hk == "closed" else cn)
    return {
        "cn": cn,
        "hk": hk,
        "overall": overall,
        "label": label,
        "date": local.strftime("%Y-%m-%d"),
        "weekday": "周" + "一二三四五六日"[weekday],
    }


def build_name_map(reports_index: list[dict]) -> dict[str, dict]:
    """reports 目录名（code.market）→ {market, dir}。无名称源——名称硬缺省 mono code（升级位）。"""
    out: dict[str, dict] = {}
    for row in reports_index or []:
        d = str(row.get("dir", ""))
        if "." in d:
            code, _, market = d.partition(".")
            out[code] = {"market": market, "dir": d}
    return out


def signals_snapshot(entries: list | None, price_map: dict[str, float | None]) -> dict:
    """signals.json 条目 → 每股末条快照（read_last_signal 同序语义）。

    现价优先级：price_map（sim last_price / facts.price 组装方注入）→ 缺省 None（呈现「—」）；
    信号词非法行降级标 invalid（呈现层降级非闸——宁拒不漏只约束出卡）。
    """
    rows: list[dict] = []
    read_ok = isinstance(entries, list)
    if not read_ok:
        return {"rows": [], "read_ok": False}
    latest: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        if not code:
            continue
        latest[code] = entry
        if code not in order:
            order.append(code)
    for code in order:
        entry = latest[code]
        signal = entry.get("signal")
        valid = signal in VALID_SIGNALS
        stop = ((entry.get("key_levels") or {}).get("stop")) or entry.get("stop")
        price = price_map.get(code)
        to_stop = None
        if isinstance(stop, (int, float)) and stop and isinstance(price, (int, float)) and price:
            to_stop = round((price / stop - 1) * 100, 2)
        rows.append(
            {
                "code": code,
                "market": entry.get("market", ""),
                "signal": signal if valid else None,
                "signal_raw": signal,
                "invalid": not valid,
                "confidence": entry.get("confidence"),
                "profile": entry.get("profile", "default"),
                "date": entry.get("date", ""),
                "stop": stop,
                "price": price,
                "to_stop_pct": to_stop,
            }
        )
    return {"rows": rows, "read_ok": True}


def derive_todos(
    events: list[dict], pending_fills: list[dict], briefing_cards: list[dict]
) -> list[dict]:
    """三源待办聚合：pending fills（人在环阻塞优先）→ mech_risk 事件 → briefing 未 ack。"""
    todos: list[dict] = []
    for req in pending_fills or []:
        if req.get("state") != "pending":
            continue
        todos.append(
            {
                "source": "pending_fill",
                "code": str(req.get("code", "")),
                "text": (
                    f"模拟成交待逐笔确认（{req.get('tranche_leg', '')}"
                    f" @ {req.get('ref_price', '')}）"
                ),
                "chip": "待确认",
            }
        )
    for ev in events or []:
        mech = ev.get("mech_risk")
        if not mech or ev.get("suppress_reason"):
            continue
        todos.append(
            {
                "source": "mech_risk",
                "code": str(ev.get("code", "")),
                "text": (
                    f"降档事实待盘后复核（{ev.get('rule', '')}"
                    f" · severity {mech.get('severity', '')}）"
                ),
                "chip": "待复核",
            }
        )
    for card in briefing_cards or []:
        if card.get("ack"):
            continue
        todos.append(
            {
                "source": "briefing",
                "code": str(card.get("code", "")),
                "text": "晨报/复盘行动项未确认",
                "chip": "待确认",
            }
        )
    return todos


def briefing_action_items(cards_md: list[str]) -> list[dict]:
    """解析 briefing 卡 md 的「## 行动项」段 `- [ ] 复核：xxx` 行（structured_output 不持久化）。"""
    out: list[dict] = []
    for md in cards_md or []:
        code = ""
        in_section = False
        for line in md.splitlines():
            if line.startswith("code:"):
                code = line.partition(":")[2].strip()
            if line.startswith("## "):
                in_section = "行动项" in line
                continue
            if in_section and line.startswith("- [ ]"):
                item = line.partition("]")[2].strip() if "]" in line else line.strip()
                out.append({"code": code, "item": item.removeprefix("复核：").strip()})
    return out
