"""预算闸三档 + 双口径记账（默认关；D2 双形态）。"""

from __future__ import annotations

from stock_trading_agent.budget.gate import BudgetConfig, check, record_dual_axis

API = BudgetConfig(enabled=True, billing_mode="api")
SUB = BudgetConfig(enabled=True, billing_mode="subscription")
OFF = BudgetConfig()


class TestGates:
    def test_disabled_always_allows(self):
        v = check(
            OFF,
            session_est_usd=10_000,
            day_spent_usd=10_000,
            month_spent_usd=10_000,
            day_task_count=10_000,
        )
        assert v.allowed

    def test_subscription_record_only(self):
        v = check(
            SUB, session_est_usd=999, day_spent_usd=999, month_spent_usd=9_999, day_task_count=0
        )
        assert v.allowed and v.record_only

    def test_subscription_hard_task_cap(self):
        v = check(SUB, session_est_usd=0.1, day_spent_usd=0, month_spent_usd=0, day_task_count=50)
        assert not v.allowed and "次数闸" in v.reason

    def test_api_per_session(self):
        v = check(API, session_est_usd=1.01, day_spent_usd=0, month_spent_usd=0, day_task_count=0)
        assert not v.allowed and "单会话" in v.reason

    def test_api_daily_envelope(self):
        v = check(API, session_est_usd=0.5, day_spent_usd=7.8, month_spent_usd=0, day_task_count=0)
        assert not v.allowed and "日累计" in v.reason

    def test_api_monthly_envelope(self):
        v = check(API, session_est_usd=0.5, day_spent_usd=0, month_spent_usd=99.8, day_task_count=0)
        assert not v.allowed and "月累计" in v.reason

    def test_api_within_all_passes(self):
        assert check(
            API, session_est_usd=0.5, day_spent_usd=1.0, month_spent_usd=10.0, day_task_count=3
        ).allowed


class TestDualAxisLedger:
    def test_accumulates_tokens_and_cost(self):
        ledger = record_dual_axis(
            {"input_tokens": 100, "output_tokens": 10}, 0.25, {}, day="2026-11-08", month="2026-11"
        )
        ledger = record_dual_axis(
            {"input_tokens": 50}, 0.35, ledger, day="2026-11-08", month="2026-11"
        )
        assert ledger["tokens"] == {"input_tokens": 150, "output_tokens": 10}
        assert ledger["cost_usd_equiv"] == 0.6
        assert ledger["tasks"] == 2
        # 0.0.9 分桶：日桶含 usd+tasks 双口径，月桶 USD（修月闸空转实 bug）
        assert ledger["daily"]["2026-11-08"] == {"usd": 0.6, "tasks": 2}
        assert ledger["monthly"]["2026-11"] == {"usd": 0.6}

    def test_defaults_from_empty(self):
        out = record_dual_axis({}, 0.0, {}, day="2026-11-08", month="2026-11")
        assert out["tokens"] == {} and out["cost_usd_equiv"] == 0.0 and out["tasks"] == 1
        assert out["daily"]["2026-11-08"]["tasks"] == 1  # 空账也记任务次数（次数闸数据源）

    def test_day_bucket_isolation(self):
        ledger = record_dual_axis({}, 0.5, {}, day="2026-11-07", month="2026-11")
        ledger = record_dual_axis({}, 1.0, ledger, day="2026-11-08", month="2026-11")
        assert ledger["daily"]["2026-11-07"]["usd"] == 0.5
        assert ledger["daily"]["2026-11-08"] == {"usd": 1.0, "tasks": 1}  # 日切隔离
        assert ledger["monthly"]["2026-11"] == {"usd": 1.5}  # 月桶跨日累计
