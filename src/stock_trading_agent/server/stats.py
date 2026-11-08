"""后验战绩聚合（面板⑥）：decisions 画像 + 预算双口径曲线（§15）。

样本 <30 只观察不记分（midterm 纪律同款：成功率标 insufficient）；「全样本登记含亏损」
由原始数据呈现（失败任务全量透出，不做择优聚合）。
"""

from __future__ import annotations

import datetime as dt
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
