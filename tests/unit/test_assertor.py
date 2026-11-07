"""NR-3 断言器判据族（只收严）。"""

from __future__ import annotations

from pathlib import Path

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.loop.assertor import (
    SENSITIVE_FIELD_BLACKLIST,
    assess_result,
    scan_sensitive_fields,
)


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

    def test_non_opinion_kind_skips_signal_check(self):
        assert assess_result(result(structured_output={"ok": True}), "paper_confirm").ok

    def test_sensitive_fields_refused(self):
        verdict = assess_result(
            result(structured_output={"signal": "hold", "disclaimer": "x", "cost_basis": 12.5}),
            "diagnose_quick",
        )
        assert not verdict.ok and any("敏感字段" in r for r in verdict.reasons)

    def test_sensitive_scan_nested_and_clean(self):
        hits = scan_sensitive_fields({"a": {"position_size": 100}, "b": [{"api_key": "k"}]})
        assert len(hits) == 2
        assert scan_sensitive_fields({"signal": "hold"}) == []
        assert "cost_basis" in SENSITIVE_FIELD_BLACKLIST
