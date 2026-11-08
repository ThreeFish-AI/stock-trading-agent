"""后验战绩聚合（面板⑥）：decisions 画像 + 预算双口径曲线（§15）。

样本 <30 只观察不记分（midterm 纪律同款：成功率标 insufficient）；「全样本登记含亏损」
由原始数据呈现（失败任务全量透出，不做择优聚合）。
"""

from __future__ import annotations

import datetime as dt
import itertools
from zoneinfo import ZoneInfo

_MARKET_TZ = ZoneInfo("Asia/Shanghai")
MIN_SAMPLES = 30  # 与 vibe-stock postmortem MIN_SAMPLES 同值（07 P3 后验纪律）


def aggregate_decisions(lines: list[dict]) -> dict:
    """decision log 画像：kind 分布 + 任务三态计数与成功率（样本不足标 insufficient）。"""
    kinds: dict[str, int] = {}
    done = failed = abandoned = 0
    for line in lines:
        kind = str(line.get("kind", ""))
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "task_done":
            done += 1
        elif kind == "task_failed":
            failed += 1
        elif kind == "task_abandoned":
            abandoned += 1
    total = done + failed + abandoned
    success_rate = round(done / total, 4) if total else None
    return {
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "done": done,
        "failed": failed,
        "abandoned": abandoned,
        "total": total,
        "success_rate": success_rate,
        "sufficient_sample": total >= MIN_SAMPLES,
        "note": None if total >= MIN_SAMPLES else f"样本 {total} < {MIN_SAMPLES}：只观察不记分",
    }


def perf_summary(equity_history: list[dict], operations: list[dict]) -> dict:
    """④绩效小卡：Sharpe（日频年化）/ 最大回撤 / 胜率（FIFO 配对）。

    样本 <MIN_SAMPLES(30) 只观察不记分（win_rate=None + insufficient note，midterm 纪律同款）；
    closed_pairs=已平仓配对数（部分卖出按卖出笔计）；Sharpe std=0 或 n<2 → None。
    """
    navs = [float(p.get("nav") or 0) for p in equity_history or []]
    sharpe = None
    if len(navs) >= 2:
        rets = [(b / a - 1) for a, b in itertools.pairwise(navs) if a]
        if len(rets) >= 2:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            std = var**0.5
            if std > 0:
                sharpe = round(mean / std * (252**0.5), 2)
    max_dd = 0.0
    peak = 0.0
    for nav in navs:
        peak = max(peak, nav)
        if peak:
            max_dd = min(max_dd, nav / peak - 1)
    # 胜率：FIFO 配对（同 code 卖出价 > 加权买入价计胜；卖出笔为样本单位）
    buy_q: dict[str, list[tuple[float, float]]] = {}
    wins = 0
    closed = 0
    for op in operations or []:
        code = str(op.get("code", ""))
        price = float(op.get("price") or 0)
        lots = int(op.get("lots") or 0)
        if not code or not price or not lots:
            continue
        if op.get("side") == "buy":
            buy_q.setdefault(code, []).append((price, lots))
        else:
            remaining = lots
            weighted_cost = 0.0
            while remaining > 0 and buy_q.get(code):
                bp, bl = buy_q[code][0]
                take = min(bl, remaining)
                weighted_cost += bp * take
                remaining -= take
                if bl > take:
                    buy_q[code][0] = (bp, bl - take)
                else:
                    buy_q[code].pop(0)
            closed += 1
            if price * lots > weighted_cost:
                wins += 1
    sufficient = closed >= MIN_SAMPLES
    return {
        "sharpe": sharpe,
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(wins / closed, 4) if closed and sufficient else None,
        "closed_pairs": closed,
        "sufficient": sufficient,
        "note": None if sufficient else f"样本 {closed} < {MIN_SAMPLES}：只观察不记分",
    }


def t1_review_hitrate(card_lines: list[dict], ledger_rows: dict) -> dict:
    """⑥T+1 复核命中率：advice_card_written 决策行 vs ledger.agent.json evaluated 投影对齐。"""
    attribution = (ledger_rows or {}).get("attribution") or {}
    wins = sum(int(v.get("wins", 0)) for v in attribution.values() if isinstance(v, dict))
    losses = sum(int(v.get("losses", 0)) for v in attribution.values() if isinstance(v, dict))
    scored = wins + losses
    sufficient = scored >= MIN_SAMPLES
    return {
        "sampled": scored,
        "hits": wins,
        "rate": round(wins / scored, 4) if scored and sufficient else None,
        "note": None if sufficient else f"样本 {scored} < {MIN_SAMPLES}：只观察不记分",
    }


def budget_curve(session_records: list[dict]) -> list[dict]:
    """会话登记 → 按市场日分桶的双口径曲线（token 数 + 等效美元，F-34）。"""
    buckets: dict[str, dict] = {}
    for record in session_records:
        ts = str(record.get("ts", ""))
        try:
            day = dt.datetime.fromisoformat(ts).astimezone(_MARKET_TZ).strftime("%Y-%m-%d")
        except ValueError:
            continue
        bucket = buckets.setdefault(
            day, {"date": day, "tokens": 0, "cost_usd_equiv": 0.0, "sessions": 0}
        )
        usage = record.get("usage_tokens") or {}
        bucket["tokens"] += sum(v for v in usage.values() if isinstance(v, int))
        bucket["cost_usd_equiv"] += float(record.get("cost_usd_equiv") or 0.0)
        bucket["sessions"] += 1
    return [buckets[d] for d in sorted(buckets)]


def paper_totals(positions: list[dict], cash: float, budget: float) -> dict:
    """④表尾汇总条（原型 paper.html）：总市值/现金/总资产/净值（呈现态原值，13-paper §⑥）。"""
    market_value = sum(float(p.get("market_value") or 0) for p in positions or [])
    total = market_value + float(cash or 0)
    return {
        "market_value": round(market_value, 2),
        "cash": round(float(cash or 0), 2),
        "total": round(total, 2),
        "nav": round(total / budget, 4) if budget else None,
    }
