"""NR-3 断言器判据族（只收严）。"""

from __future__ import annotations

from pathlib import Path

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.loop.assertor import (
    SENSITIVE_KEY_PATTERN,
    assess_result,
    scan_sensitive_fields,
)

OPINION_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["signal", "disclaimer"],
    "properties": {
        "signal": {"enum": ["strong_buy", "buy", "hold", "sell", "strong_sell"]},
        "disclaimer": {"type": "string", "minLength": 1},
    },
}


def result(**over) -> CCResult:
    base = dict(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output={"signal": "hold", "disclaimer": "不构成投资建议"},
        result_line_present=True,
        transcript_path=Path("/dev/null"),
    )
    base.update(over)
    return CCResult(**base)


class TestThreeSegment:
    def test_ok_opinion_passes(self):
        assert assess_result(result(), "diagnose_quick").ok

    def test_exit_zero_but_no_result_line(self):
        verdict = assess_result(result(result_line_present=False), "diagnose_quick")
        assert not verdict.ok and any("result 行" in r for r in verdict.reasons)

    def test_missing_envelope(self):
        assert not assess_result(result(envelope=None), "diagnose_quick").ok

    def test_nonzero_exit(self):
        assert not assess_result(result(exit_code=1), "diagnose_quick").ok

    def test_success_without_structured_output_fails(self):
        # strict 静默回退不可自证（F-14/F-15）
        verdict = assess_result(result(structured_output=None), "diagnose_quick")
        assert not verdict.ok and any("structured_output" in r for r in verdict.reasons)


class TestRedlineFamily:
    def test_signal_must_be_five_word_enum(self):
        verdict = assess_result(
            result(structured_output={"signal": "买入", "disclaimer": "x"}), "diagnose_quick"
        )
        assert not verdict.ok and any("五词枚举" in r for r in verdict.reasons)

    def test_opinion_needs_disclaimer(self):
        verdict = assess_result(result(structured_output={"signal": "hold"}), "diagnose_quick")
        assert not verdict.ok and any("免责段" in r for r in verdict.reasons)

    def test_every_kind_needs_disclaimer(self):
        # §7 免责四件套进全部产物：monitor_review / paper_confirm / briefing_review 无免责段同样拒
        for kind in ("monitor_review", "paper_confirm", "briefing_review"):
            verdict = assess_result(result(structured_output={"ok": True}), kind)
            assert not verdict.ok and any("免责段" in r for r in verdict.reasons), kind

    def test_non_opinion_kind_with_disclaimer_passes(self):
        assert assess_result(
            result(structured_output={"ok": True, "disclaimer": "x"}), "paper_confirm"
        ).ok

    def test_sensitive_fields_refused(self):
        verdict = assess_result(
            result(structured_output={"signal": "hold", "disclaimer": "x", "cost_basis": 12.5}),
            "diagnose_quick",
        )
        assert not verdict.ok and any("敏感字段" in r for r in verdict.reasons)

    def test_sensitive_scan_variant_keys_caught(self):
        # 同义/大小写/驼峰变体必须命中（子串匹配防绕过）
        payload = {
            "Quantity": 100,
            "avgCost": 9.6,
            "total_cost": 1200,
            "shares": 3,
            "market_value": 3000,
            "pnl": -12.5,
            "sellable_lots": 2,
            "nested": [{"qty": 1}],
        }
        hits = scan_sensitive_fields(payload)
        assert len(hits) >= 7, hits

    def test_sensitive_scan_clean_payload(self):
        assert scan_sensitive_fields({"signal": "hold", "rationale": "…", "disclaimer": "x"}) == []

    def test_sensitive_pattern_word_samples(self):
        for key in ("cost", "cost_basis", "api_key", "apiKey", "Quantity", "holding_pct"):
            assert SENSITIVE_KEY_PATTERN.search(key), key
        for key in ("signal", "rationale", "disclaimer", "kind"):
            assert not SENSITIVE_KEY_PATTERN.search(key), key


class TestSchemaRevalidation:
    """draft-07 本地复验（NR-3）：喂给 CC 的 schema 同份复验 structured_output。"""

    def test_conforming_passes(self):
        out = {"signal": "hold", "disclaimer": "x"}
        assert assess_result(
            result(structured_output=out), "diagnose_quick", schema=OPINION_SCHEMA
        ).ok

    def test_type_violation_refused(self):
        out = {"signal": "hold", "disclaimer": 42}  # 类型错
        verdict = assess_result(
            result(structured_output=out), "diagnose_quick", schema=OPINION_SCHEMA
        )
        assert not verdict.ok and any("schema 复验失败" in r for r in verdict.reasons)

    def test_required_missing_refused(self):
        out = {"disclaimer": "x"}  # 缺 required signal
        verdict = assess_result(
            result(structured_output=out), "diagnose_quick", schema=OPINION_SCHEMA
        )
        assert not verdict.ok and any("schema 复验失败" in r for r in verdict.reasons)

    def test_no_schema_skips_revalidation(self):
        out = {"signal": "hold", "disclaimer": "x"}
        assert assess_result(result(structured_output=out), "diagnose_quick").ok
