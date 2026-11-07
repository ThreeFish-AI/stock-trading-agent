"""预算闸纯逻辑（默认关；订阅/计费双形态机，方案 §13 与 D2）。

D2（已裁定）：①全部调用走订阅登录态 → 数值放开（--max-budget-usd 仍带高位哨兵满足 NR-4
旗标在场性，日/月 USD 信封不设闸、仅记账，硬性次数闸保留）；②任一调用走 API Usage 计费 →
单会话 $1、日累计 $8、月 $100 量级（estimated 占位）。形态由 probe() 认证态机判，不凭配置自述。
坑位（F-18）：真实上限 = cap + 最贵单次调用（实测 $1 顶停于 $1.38）。
"""

from __future__ import annotations

from dataclasses import dataclass

BillingMode = str  # "subscription" | "api"

API_GATES = {"per_session_usd": 1.0, "daily_usd": 8.0, "monthly_usd": 100.0}


@dataclass
class BudgetConfig:
    billing_mode: BillingMode = "subscription"  # 骨架默认 subscription 占位（真实判定属 probe()）
    enabled: bool = False  # 默认关（M3 无人值守才开启；人在环期仅记账）
    per_session_usd: float = 1.0
    daily_usd: float = 8.0
    monthly_usd: float = 100.0
    max_tasks_per_day: int = 50


@dataclass(frozen=True)
class GateVerdict:
    allowed: bool
    reason: str = ""
    record_only: bool = False  # True = 越限仅记账不拒（订阅形态 D2①）


def check(
    cfg: BudgetConfig,
    *,
    session_est_usd: float,
    day_spent_usd: float,
    month_spent_usd: float,
    day_task_count: int,
) -> GateVerdict:
    """发送前预检（外闸）：enabled=False 全放行；订阅形态越限仅记账；API 形态三档拒 + 硬次数闸。"""
    if not cfg.enabled:
        return GateVerdict(allowed=True, reason="预算闸默认关（人在环期仅记账）")
    if cfg.billing_mode == "subscription":
        if day_task_count >= cfg.max_tasks_per_day:
            return GateVerdict(
                allowed=False,
                reason=f"硬性次数闸：当日任务数 {day_task_count} ≥ {cfg.max_tasks_per_day}",
            )
        return GateVerdict(allowed=True, reason="订阅形态数值放开、仅记账（D2①）", record_only=True)
    # API 计费形态（D2② estimated 占位）
    if day_task_count >= cfg.max_tasks_per_day:
        return GateVerdict(
            allowed=False,
            reason=f"硬性次数闸：当日任务数 {day_task_count} ≥ {cfg.max_tasks_per_day}",
        )
    if session_est_usd > API_GATES["per_session_usd"]:
        return GateVerdict(
            allowed=False,
            reason=f"单会话估算 {session_est_usd:.2f} > ${API_GATES['per_session_usd']}",
        )
    if day_spent_usd + session_est_usd > API_GATES["daily_usd"]:
        return GateVerdict(
            allowed=False,
            reason=f"日累计 {day_spent_usd:.2f}+{session_est_usd:.2f} > ${API_GATES['daily_usd']}",
        )
    if month_spent_usd + session_est_usd > API_GATES["monthly_usd"]:
        return GateVerdict(allowed=False, reason=f"月累计超 ${API_GATES['monthly_usd']}")
    return GateVerdict(allowed=True)


def record_dual_axis(tokens: dict[str, int], cost_usd_equiv: float, ledger: dict) -> dict:
    """订阅轨双口径记账（F-34：token 数 + 等效美元同记；纯累加）。"""
    totals = dict(ledger.get("tokens", {}))
    for key, val in tokens.items():
        totals[key] = totals.get(key, 0) + val
    return {
        "tokens": totals,
        "cost_usd_equiv": round(ledger.get("cost_usd_equiv", 0.0) + cost_usd_equiv, 4),
        "tasks": ledger.get("tasks", 0) + 1,
    }
