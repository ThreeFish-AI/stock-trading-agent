"""呈现层纯函数（零 IO 判据 + 薄读取器）：盘态/名称映射/信号快照/待办聚合/晨报行动项。

12-orchestration §⑫ 落点表。纪律：外仓 schema（signals.json 等）defensive 解析缺席降级
不炸页；root 直拼只在 server 层（state/ 禁字面量红线扫描纪律不动）。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from stock_trading_agent.loop.advice import has_review, parse_advice_card
from stock_trading_agent.loop.events import MARKET_TZ

VALID_SIGNALS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
MIN_SHOW_DAYS = 0  # 全部在册信号展示（无隐藏）


# --- 现价源三族纯函数（12-orchestration §⑫ 现价源升级位 0.0.25：零网络，三源本地台账/产物） ---


@dataclass(frozen=True)
class PriceFact:
    """单源现价事实：价 + asof 时戳（ISO，缺席 ""）+ 源标签。"""

    price: float
    asof: str = ""
    source: str = ""


def parse_ticks_prices(raw_lines: list[str]) -> dict[str, PriceFact]:
    """monitor/ticks/<date>.jsonl 尾扫现价：坏行跳过、同 code 后行覆盖（台账即时间序）。

    行 schema（watch.py 写者）：{code, price, ts_utc8, local_ts, ...}；ts_utc8 优先作 asof。
    """
    out: dict[str, PriceFact] = {}
    for line in raw_lines or []:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        price = row.get("price")
        if not code or not isinstance(price, (int, float)) or isinstance(price, bool):
            continue
        out[code] = PriceFact(
            price=float(price),
            asof=str(row.get("ts_utc8") or row.get("local_ts") or ""),
            source="ticks",
        )
    return out


def t1_price(payload: dict) -> PriceFact | None:
    """T1 evidence 价：blocks.quote.price（state=available 才取）+ generated_at asof。"""
    if not isinstance(payload, dict):
        return None
    quote = ((payload.get("blocks") or {}).get("quote")) or {}
    price = quote.get("price")
    state = quote.get("state")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or state != "available":
        return None
    return PriceFact(price=float(price), asof=str(payload.get("generated_at") or ""), source="t1")


def _asof_dt(asof: str) -> dt.datetime | None:
    """asof 宽松解析：ISO（含 T）与「YYYY-MM-DD HH:MM:SS」皆可；无时区按市场钟面；坏串 None。"""
    if not asof:
        return None
    try:
        ts = dt.datetime.fromisoformat(asof)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=MARKET_TZ)


def merge_price_facts(*maps: dict[str, PriceFact]) -> dict[str, PriceFact]:
    """多源合并：asof 时序最新者优先（按 datetime 解析比较——空间隔与 ISO T 两种形态
    字典序不可比）；asof 缺席排最后、组内按实参序后到覆盖；坏串 asof 视同缺席。

    时间序即正确性（ticks 轮询价最即时、t1 取证时价次之、sim 确认 bar 价最滞后）；
    固定源序会在「T1 刚刷新而 ticks 停更」场景给出旧价。调用方按任意序传入皆可。
    """

    def _wins(cand: PriceFact, cur: PriceFact) -> bool:
        cand_ts, cur_ts = _asof_dt(cand.asof), _asof_dt(cur.asof)
        if (cand_ts is None) != (cur_ts is None):
            return cand_ts is not None  # 可解析者恒胜
        if cand_ts is None:
            return True  # 双不可解析：后到覆盖
        return cand_ts >= cur_ts  # 新者胜、平手后到胜

    out: dict[str, PriceFact] = {}
    for m in maps:
        for code, fact in (m or {}).items():
            cur = out.get(code)
            if cur is None or _wins(fact, cur):
                out[code] = fact
    return out


def asof_stale(asof: str, market_date: str) -> bool:
    """陈旧判定：asof 换算市场钟面后非市场今日；坏串/缺席保守视陈旧。

    无时区串（ticks ts_utc8「YYYY-MM-DD HH:MM:SS」）按市场钟面解读——CI 时区无关。
    """
    ts = _asof_dt(asof)
    if ts is None:
        return True
    return ts.astimezone(MARKET_TZ).strftime("%Y-%m-%d") != market_date


def load_root_json(path: Path) -> list | dict | None:
    """薄读取器：缺席/坏 JSON → None（调用方降级空态）；R2 走 store 的 mtime+size 短路缓存。"""
    from stock_trading_agent.state.store import read_json_cached

    return read_json_cached(path)


NOTIFY_TAIL = (
    " ·待收盘复核·非投资建议"  # 与 vibe-stock watch.py NOTIFY_TAIL 对齐（只读重建用，指针非复制）
)
NOTIFY_TITLE_DEFAULT = "vibe-stock 盯盘"


def load_name_map(layout, *, signals: list | dict | None = None) -> dict[str, str]:
    """名称源回退链（产品仓只读；12-orchestration §⑫ 升级位落地）：
    names.json（主，report 写穿）→ signals.json 最新 stock_name → universe rows name。
    signals 参数：调用方已读入的 signals.json 内容注入（R4 单请求去重复读；None 才自读）。
    """
    names: dict[str, str] = {}
    primary = load_root_json(layout.root / "names.json")
    if isinstance(primary, dict):
        for key, value in primary.items():
            if isinstance(value, str) and value:
                _, _, code = str(key).rpartition(":")
                if code:
                    names[code] = value
    if signals is None:
        signals = load_root_json(layout.root / "signals.json")
    if isinstance(signals, list):
        for entry in signals:
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("code") or "")
            stock_name = entry.get("stock_name")
            if code and isinstance(stock_name, str) and stock_name and code not in names:
                names[code] = stock_name
    universe = load_root_json(layout.monitor_dir / "universe.json")
    if isinstance(universe, dict):
        for row in universe.get("rows") or []:
            if isinstance(row, dict):
                code = str(row.get("code") or "")
                name = row.get("name")
                if code and isinstance(name, str) and name and code not in names:
                    names[code] = name
    return names


def aggregate_calendar(evidence_payloads: list[dict]) -> list[dict]:
    """事件日历聚合：多份 evidence payload（T1/会话 workdir 防重去重取最新）→ 日期升序 cap 15。

    源块：forecast.next_disclosure.appoint_date（预约披露）/ unlock.upcoming（解禁前 3）/
    notices.items（近 7 天公告）。A 股专属块对港股 not_supported 如实空。
    """
    today = dt.datetime.now(tz=MARKET_TZ).strftime("%Y-%m-%d")
    out: list[dict] = []
    seen: set[tuple] = set()
    for payload in evidence_payloads:
        blocks = (payload or {}).get("blocks") or {}
        meta = (payload or {}).get("meta")
        code = (meta.get("code") if isinstance(meta, dict) else None) or ""
        if not code:
            continue
        forecast = blocks.get("forecast") or {}
        nd = forecast.get("next_disclosure") or {}
        appoint = nd.get("appoint_date")
        if appoint and appoint >= today:
            key = (appoint, "disclosure", code)
            if key not in seen:
                seen.add(key)
                out.append(
                    {"date": appoint, "type": "披露", "code": code, "detail": "定期报告预约披露日"}
                )
        unlock = blocks.get("unlock") or {}
        for item in (unlock.get("upcoming") or [])[:3]:
            date = item.get("date")
            if not date or date < today:
                continue
            ratio = item.get("ratio_pct")
            key = (date, "unlock", code)
            if key not in seen:
                seen.add(key)
                detail = (
                    f"解禁占流通 {ratio}%（{item.get('type', '')}）"
                    if ratio is not None
                    else "解禁"
                )
                out.append({"date": date, "type": "解禁", "code": code, "detail": detail})
        notices = blocks.get("notices") or {}
        for item in notices.get("items") or []:
            date = item.get("date")
            if not date or date < today:
                continue
            key = (date, str(item.get("title", ""))[:20], code)
            if key not in seen:
                seen.add(key)
                out.append(
                    {
                        "date": date,
                        "type": "公告",
                        "code": code,
                        "detail": f"{item.get('type', '')} {item.get('title', '')}".strip(),
                    }
                )
    out.sort(key=lambda r: r["date"])
    return out[:15]


def rebuild_notifications(
    events: list[dict], watch_log_text: str, names: dict[str, str]
) -> list[dict]:
    """已发通知历史重放（只读；产品仓永不发通知，R7）。

    双源：events 台账 notify=true 行（构造 body）+ watch.out.log 的 notify 行（终态原文，优先）。
    """
    out: list[dict] = []
    seen_keys: set[str] = set()
    for line in (watch_log_text or "").splitlines():
        idx = line.find("notify: ")
        if idx < 0:
            continue
        raw = line[idx + len("notify: ") :]
        title = NOTIFY_TITLE_DEFAULT
        body = raw
        if raw.startswith("[") and "]" in raw:
            end = raw.index("]")
            title = raw[1:end]
            body = raw[end + 1 :].strip()
        ts = line[:idx].strip() or ""
        key = f"log:{body[:40]}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append({"ts": ts, "code": "", "title": title, "body": body, "source": "log"})
    for ev in events or []:
        if not ev.get("notify") or ev.get("suppress_reason"):
            continue
        code = str(ev.get("code", ""))
        name = names.get(code, code)
        review = f" ·{ev['review']}" if ev.get("review") else ""
        body = f"{name} {ev.get('rule', '')}（{ev.get('ts', '')}）{review}{NOTIFY_TAIL}"
        key = f"ev:{code}:{ev.get('ts', '')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            {
                "ts": ev.get("ts", ""),
                "code": code,
                "title": NOTIFY_TITLE_DEFAULT,
                "body": body,
                "source": "events",
            }
        )
    return out[-20:]


def market_phase(now: dt.datetime | None = None) -> dict:
    """盘态判定（午休分态 0.0.25：CN 11:30–13:00 / HK 12:00–13:00，纯呈现粒度、闸与调度零影响）。"""
    if now is None:
        now = dt.datetime.now(tz=dt.UTC)
    local = now.astimezone(MARKET_TZ)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()

    def phase_of(open_m: int, close_m: int, lunch: tuple[int, int]) -> str:
        if weekday >= 5:
            return "closed"
        if minute < open_m:
            return "pre"
        if minute >= close_m:
            return "post"
        if lunch[0] <= minute < lunch[1]:
            return "lunch"
        return "live"

    cn = phase_of(9 * 60 + 30, 15 * 60, (11 * 60 + 30, 13 * 60))
    hk = phase_of(9 * 60 + 30, 16 * 60, (12 * 60, 13 * 60))
    if "live" in (cn, hk):
        label = "盘中"
    elif "lunch" in (cn, hk):
        label = "午休"
    elif cn == hk == "closed":
        label = "休市"
    elif "pre" in (cn, hk):
        label = "盘前"
    else:
        label = "盘后"
    if "live" in (cn, hk):
        overall = "live"
    elif "lunch" in (cn, hk):
        overall = "lunch"
    elif cn == hk == "closed":
        overall = "closed"
    else:
        overall = cn
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


def signals_snapshot(
    entries: list | None,
    price_map: dict[str, PriceFact | float | None],
    *,
    market_date: str = "",
) -> dict:
    """signals.json 条目 → 每股末条快照（read_last_signal 同序语义）。

    现价入参双形态：PriceFact（三源合并产物，带 asof/source/stale）或裸 float（无 asof，
    price_asof/price_source 为空、price_stale=False）→ 缺省 None（呈现「—」）；
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
        fact = price_map.get(code)
        if isinstance(fact, PriceFact):
            price: float | None = fact.price
            price_asof, price_source = fact.asof, fact.source
        elif isinstance(fact, (int, float)) and not isinstance(fact, bool):
            price = float(fact)
            price_asof, price_source = "", ""
        else:
            price = None
            price_asof, price_source = "", ""
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
                "price_asof": price_asof,
                "price_source": price_source,
                "price_stale": (
                    asof_stale(price_asof, market_date) if price_asof and market_date else False
                ),
                "to_stop_pct": to_stop,
            }
        )

    # 0.0.24 展示层聚合排序：breach → near → 常序 → 无价 → invalid 置尾
    # （组内保登记序——Python 稳定排序；纯呈现层，/api/signals JSON 无消费者钉序）。
    def _rank(row: dict) -> int:
        t = row.get("to_stop_pct")
        if row.get("invalid"):
            return 4
        if t is None:
            return 3
        if t <= 0:
            return 0  # breach
        if t <= 3:
            return 1  # near
        return 2

    rows.sort(key=_rank)
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


def latest_advice_reference(advice_dir: Path, decision_lines: list[dict]) -> tuple[str, str] | None:
    """③页复核区默认注入目标（0.0.37）：最新建议卡。

    近 5 个日期目录内优先未复核卡（mtime 最新者胜），全部已复核回落最新卡；
    无卡返回 None（端点回空态卡）。纯函数（目录 + decisions 入参），单测直测。
    """
    if not advice_dir.exists():
        return None
    best_unreviewed: tuple[dt.datetime, str, str] | None = None  # (mtime, date, code)
    fallback: tuple[dt.datetime, str, str] | None = None
    day_dirs = sorted(
        (p for p in advice_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True
    )[:5]
    for day_dir in day_dirs:
        day = day_dir.name
        for card_path in day_dir.glob("*.md"):
            meta = parse_advice_card(card_path.read_text(encoding="utf-8"), stem=card_path.stem)
            if not meta:
                continue
            code = str(meta.get("code") or card_path.stem)
            mtime = dt.datetime.fromtimestamp(card_path.stat().st_mtime, tz=dt.UTC)
            entry = (mtime, day, code)
            if has_review(decision_lines, date=day, code=code):
                if fallback is None or entry > fallback:
                    fallback = entry
            elif best_unreviewed is None or entry > best_unreviewed:
                best_unreviewed = entry
    picked = best_unreviewed or fallback
    return (picked[1], picked[2]) if picked else None
