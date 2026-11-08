"""0.0.22 观测指标族：断言违例分桶 / 路由命中率 / per-kind 成本分布（纯函数）+ typed codes。"""

from __future__ import annotations

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.loop.assertor import AdviceContext, assess_result
from stock_trading_agent.server.stats import assertion_buckets, cost_by_kind, route_hit_rate


def _result(**kw) -> CCResult:
    base = dict(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output={"disclaimer": "x"},
        result_line_present=True,
        transcript_path="/tmp/t.jsonl",
        usage_tokens={},
    )
    base.update(kw)
    return CCResult(**base)


class TestTypedCodes:
    def test_three_stage_codes(self):
        v = assess_result(
            _result(
                exit_code=1,
                envelope=None,
                result_line_present=False,
                structured_output={"signal": "hold", "disclaimer": "x"},
            ),
            "diagnose_quick",
        )
        assert v.codes == ("exit_nonzero", "envelope_missing", "result_line_missing")

    def test_structured_missing_and_subtype(self):
        v = assess_result(_result(structured_output=None), "diagnose_quick")
        assert "structured_missing" in v.codes
        v2 = assess_result(_result(envelope={"subtype": "error_max_turns"}), "diagnose_quick")
        assert "result_subtype_error" in v2.codes

    def test_signal_enum_and_sensitive(self):
        so = {"signal": " buying ", "disclaimer": "ok", "avgCost": 3}
        v = assess_result(_result(structured_output=so), "diagnose_quick")
        assert "signal_enum_invalid" in v.codes and "sensitive_field" in v.codes

    def test_advice_family_codes(self):
        ctx = AdviceContext(today="2026-11-08", last_signal="hold", signals_read_ok=True)
        so = {
            "outcome": "card",
            "candidate_action": "持有",
            "final_signal": "buy",  # anchor_escalation：buy 比 hold 激进
            "verdict_date": "2026-11-07",  # stale_data
            "opinion_data_date": "2026-11-08",
            "disclaimer": "ok",
        }
        v = assess_result(_result(structured_output=so), "monitor_review", advice_ctx=ctx)
        assert "anchor_escalation" in v.codes and "stale_data" in v.codes

    def test_refuse_compat_uncategorized(self):
        from stock_trading_agent.loop.assertor import AssertVerdict

        v = AssertVerdict.refuse("自定义检查")
        assert v.codes == ("uncategorized",)


class TestAggregations:
    def _lines(self):
        return [
            {"kind": "task_submitted", "payload": {}},
            {"kind": "task_submitted", "payload": {}},
            {
                "kind": "assert_refuse",
                "payload": {"reason_codes": ["exit_nonzero", "mcp_server_errors"]},
            },
            {"kind": "advice_refused", "payload": {"reason_codes": ["stale_data"]}},
            {"kind": "assert_refuse", "payload": {"reasons": ["legacy"]}},  # 无码存量
            {
                "kind": "task_failed",
                "payload": {"reason_code": "route_unknown", "reasons": ["x"]},
            },
        ]

    def test_assertion_buckets(self):
        b = assertion_buckets(self._lines())
        assert b == {"exit_nonzero": 1, "mcp_server_errors": 1, "stale_data": 1, "uncategorized": 1}

    def test_route_hit_rate(self):
        r = route_hit_rate(self._lines())
        assert r["submitted"] == 2 and r["fallback"] == 1 and r["rate"] == 0.5
        assert route_hit_rate([])["rate"] is None

    def test_cost_by_kind_join_and_unknown(self):
        sessions = [
            {"task_id": "t1", "usage_tokens": {"total": 100}, "cost_usd_equiv": 0.1},
            {"task_id": "t2", "usage_tokens": {"total": 50}, "cost_usd_equiv": 0.2},
            {"task_id": "gone", "usage_tokens": {"total": 7}, "cost_usd_equiv": 0.01},
        ]
        tasks = [
            {"task_id": "t1", "kind": "diagnose_quick"},
            {"task_id": "t2", "kind": "monitor_review"},
        ]
        rows = cost_by_kind(sessions, tasks)
        by = {r["kind"]: r for r in rows}
        assert by["diagnose_quick"]["tokens"] == 100 and by["diagnose_quick"]["sessions"] == 1
        assert by["monitor_review"]["usd"] == 0.2
        assert by["unknown"]["sessions"] == 1  # join 不上不丢账
        assert rows[0]["kind"] == "monitor_review"  # usd 降序
