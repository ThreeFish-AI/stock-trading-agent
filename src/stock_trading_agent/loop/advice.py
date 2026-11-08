"""T2 建议卡（monitor-advice/1）：渲染/解析/脱敏扫描/写者例外/准入闸（07 P2 契约，NR-1 编排侧）。

卡面四要素+免责尾注（07）：候选动作（vibe-stock references/04 持仓者五档分类学措辞逐字，枚举
禁自造）+ 与上一裁决差异 + T+1 可卖（脱敏：只落百分比与档位词）+ 走势参考节（零权重三声明，
产品侧等义短句+vibe-stock 绝对 URL 指针，禁转述外仓规约句原文）。
脱敏边界（R2/NR-5）：monitor/ 落盘产物零成本/零数量/零可反推派生量——份额原值禁入
（渲染层单位正则负断言 + 断言层键扫双保险）；卡由断言器放行后主 Agent 原子写入
（agent/ 外唯一写者例外，写者=watch.py 的 monitor/ 其余产物不受影响）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import QueueState, TaskRecord
from stock_trading_agent.state.store import atomic_write_text

MONITOR_REVIEW_KIND = "monitor_review"
ADVICE_SCHEMA_ID = "monitor-advice/1"

# vibe-stock references/04 持仓者五档（含「等回踩」变体）逐字枚举——单测钉死防漂移
ADVICE_ACTIONS: tuple[str, ...] = (
    "可加仓",
    "持有",
    "持有·等回踩",
    "持有观察",
    "减仓",
    "离场",
)

# 走势参考三声明（产品侧等义短句；口径 SSOT 在 vibe-stock references/09，此处只留语义与指针）
TREND_SOURCE_URL = "https://github.com/ThreeFish-AI/vibe-stock/blob/main/references/09-outlook.md"
ZERO_WEIGHT_NOTE = "零权重参考：不进裁决聚合、不构成信号、不进后验记分。"
BAND_NOTE = "预测带≠后验判定带：本带只描述价格分布量级。"
BETA_NOTE = "预测带未剔除大盘 beta（个股口径）。"
DISCLAIMER_FOOTER = (
    "本卡由 Stock Trading Agent 经 vibe-stock adjudicate 原命令重裁决产出（NR-1），"
    "仅供学习与研究参考，不构成投资建议；系统不下单、不改单、不撤单、不保证收益；"
    "AI 生成，用户复核前置；盘中结论以收盘复核为准。"
)

# 份额原值负断言：卡面禁「1200 股 / 3 手 / 200 份」类数量词（脱敏边界，宁误拒不漏放）
_SHARE_UNIT_RE = re.compile(r"\d{1,7}(?:\.\d+)?\s*(?:股|份|手)")
_TREND_HORIZONS = ("1d", "3d", "5d", "10d")


def render_advice_card(structured: dict, *, code: str, date: str, task_id: str) -> str:
    """structured（outcome=card）→ 卡面 md。front-matter 全脱敏；三声明与尾注由常量注入。"""
    prev_diff = structured.get("prev_diff") or {}
    trend = structured.get("trend_ref") or {}
    rows = trend.get("band_rows") or []
    table = ["| horizon | 带下沿 | 带上沿 | 触下沿概率 |", "| --- | --- | --- | --- |"]
    for row in rows:
        table.append(
            f"| {_esc(row.get('horizon', ''))} | {_esc(row.get('low', ''))} "
            f"| {_esc(row.get('high', ''))} | {_esc(row.get('prob_touch_low_pct', ''))}% |"
        )
    lines = [
        "---",
        f"card: {ADVICE_SCHEMA_ID}",
        f"code: {code}",
        f"date: {date}",
        f"task_id: {task_id}",
        f"candidate_action: {structured.get('candidate_action', '')}",
        f"final_signal: {structured.get('final_signal', '')}",
        f"prev_final_signal: {prev_diff.get('prev_final_signal', '')}",
        f"t1_sell_pct: {structured.get('t1_sell_pct', '')}",
        f"t1_sell_tier: {structured.get('t1_sell_tier', '')}",
        f"verdict_date: {structured.get('verdict_date', '')}",
        "written_by: stock-trading-agent",
        "---",
        "",
        "## 候选动作（五档措辞 · 枚举源 vibe-stock/04）",
        f"{structured.get('candidate_action', '')}",
        "",
        "## 与上一裁决差异（adjudicate 原命令重裁决产出，非模型口述 · NR-1）",
        f"{prev_diff.get('prev_final_signal', 'none')} → {structured.get('final_signal', '')}"
        f" · {prev_diff.get('note', '')}",
        "",
        "## T+1 可卖（脱敏口径：百分比与档位词）",
        f"{structured.get('t1_sell_pct', '')}% · {structured.get('t1_sell_tier', '')}",
        "",
        "## 走势参考（零权重 · 参考非信号）",
        *table,
        f"- {ZERO_WEIGHT_NOTE}",
        f"- {BAND_NOTE}",
        f"- {BETA_NOTE}",
        f"- 口径来源：{TREND_SOURCE_URL}",
        "",
        "## 理由（LLM 补充，非裁决源）",
        str(structured.get("rationale", "")),
        "",
        "---",
        DISCLAIMER_FOOTER,
    ]
    return "\n".join(lines) + "\n"


def _esc(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def parse_advice_card(text: str) -> dict:
    """front-matter 反解（面板/测试用）。无 front-matter 返回空 dict。"""
    if not text.startswith("---"):
        return {}
    import yaml

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1]) or {}
    if isinstance(meta, dict):
        # yaml 把 6 位数字反解为 int——code 语义稳定为 str（面板/消费方免类型分叉）
        if "code" in meta:
            meta["code"] = str(meta["code"])
        return meta
    return {}


def parse_trend_table(md: str) -> list[dict]:
    """卡面 md「## 走势参考」σ√h 管道表 → band_rows（render_advice_card 的 round-trip 反解）。

    无节/无表 → []；转义管道符（\\|）由单元格 unescape 还原；坏行跳过不炸。
    """
    rows: list[dict] = []
    in_section = False
    for line in md.splitlines():
        if line.startswith("## "):
            in_section = "走势参考" in line
            continue
        if not in_section or not line.strip().startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|") for c in line.strip().strip("|").split("|")]
        if (
            len(cells) < 4
            or cells[0] in ("horizon", "---", ":---")
            or set(cells[0]) <= {"-", ":", " "}
        ):
            continue
        try:
            rows.append(
                {
                    "horizon": cells[0],
                    "low": float(cells[1]),
                    "high": float(cells[2]),
                    "prob_touch_low_pct": float(cells[3].rstrip("%")),
                }
            )
        except ValueError:
            continue
    return rows


def scan_advice_text(md: str) -> list[str]:
    """渲染层脱敏负断言（双保险第二层）：命中即拒（宁误拒不漏放）。"""
    reasons: list[str] = []
    if _SHARE_UNIT_RE.search(md):
        reasons.append("卡面出现份额/数量单位词（股|份|手）——脱敏边界违例（R2/NR-5）")
    for note, label in (
        (ZERO_WEIGHT_NOTE, "零权重声明"),
        (BAND_NOTE, "预测带声明"),
        (BETA_NOTE, "beta 声明"),
        (DISCLAIMER_FOOTER, "免责尾注"),
    ):
        if note and note not in md:  # 空串恒子串：常量被置空视为缺失（负断言不可静默通过）
            reasons.append(f"卡面缺{label}")
        if not note:
            reasons.append(f"卡面{label}常量为空")
    return reasons


def write_advice_card(layout: StateLayout, date: str, code: str, md: str) -> Path:
    """写者例外唯一入口：断言器放行后原子写（os.replace；同 code 同日重跑覆盖）。"""
    path = layout.advice_card_path(date, code)
    atomic_write_text(path, md)
    return path


def read_last_signal(root: Path, code: str) -> tuple[str | None, bool]:
    """该股最近已登记信号（只读 signals.json，写者恒为 vibe-stock report 段，R3/R8）。

    返回 (signal | None, read_ok)：文件缺席→(None, True) 放行；在场不可解析或该股条目
    信号词非法→(None, False) 拒（锚③不可比即不出卡，宁拒不漏）。
    坑位：本函数在 loop/ 层直拼文件名——红线扫描的 signals/ledger 字面量禁令只覆盖
    state/ 目录（test_redline_scan.py），state/ 层零字面量纪律不变。
    """
    path = root / "signals.json"
    if not path.exists():
        return None, True
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, False
    if not isinstance(entries, list):
        return None, False
    latest = None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("code") != code:
            continue
        signal = entry.get("signal")
        if signal is None:
            continue
        if signal not in ("strong_buy", "buy", "hold", "sell", "strong_sell"):
            return None, False
        latest = signal  # entries 顺序即登记顺序，取最后一条
    return latest, True


def universe_real_codes(layout: StateLayout) -> set[str] | None:
    """monitor/universe.json 中 kind==real 的 code 集（实盘优先判据）。

    缺席/不可解析→None（调用方：超额+None=宁拒不漏；未超额不消费本函数）。
    """
    path = layout.monitor_dir / "universe.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        return {str(r.get("code")) for r in rows if isinstance(r, dict) and r.get("kind") == "real"}
    except (json.JSONDecodeError, OSError, AttributeError):
        return None


def count_t2_done(queue: QueueState, *, today: str) -> int:
    """当日 done 的 monitor_review 任务数（5 票闸计数口径，含 facts_only 降级——花过预算即计）。

    按 enqueued_at 换算市场钟面（Asia/Shanghai）日期匹配；坑位：ingest 的 dedup_key 用
    UTC 日期与 market_today 存在既有时区日界差，此处以 enqueued_at 换算规避。
    """
    import datetime as dt
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    count = 0
    for task in queue.tasks:
        if task.kind != MONITOR_REVIEW_KIND or task.status != "done":
            continue
        if not task.enqueued_at:
            continue
        try:
            ts = dt.datetime.fromisoformat(task.enqueued_at)
        except ValueError:
            continue
        if ts.astimezone(tz).strftime("%Y-%m-%d") == today:
            count += 1
    return count


def t2_admission(task: TaskRecord, *, done_count: int, real_codes: set[str] | None) -> str | None:
    """T2 准入闸（消费期判定）：None=放行，str=拒因。每日 ≤5 票，超额仅放行实盘（07 硬前置）。"""
    if done_count < 5:
        return None
    if real_codes is None:
        return "T2 每日 5 票已满且 universe 不可读——宁拒不漏（07 实盘优先）"
    if task.code in real_codes:
        return None
    return "T2 每日 5 票已满：非实盘标的超额降级（07 实盘优先）"


def advice_context(layout: StateLayout, code: str):
    """组装断言器 AdviceContext（today=市场钟面当日 + 锚③ 最近信号只读读取）。"""
    from stock_trading_agent.loop.assertor import AdviceContext
    from stock_trading_agent.loop.monitor_events import market_today

    last_signal, read_ok = read_last_signal(layout.root, code)
    return AdviceContext(today=market_today(), last_signal=last_signal, signals_read_ok=read_ok)
