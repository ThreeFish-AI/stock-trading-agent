"""T2 建议卡纯函数族：五档常量钉死、渲染脱敏负断言、锚③矩阵、保鲜门、5 票闸。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.loop.advice import (
    ADVICE_ACTIONS,
    advice_context,
    count_t2_done,
    has_review,
    parse_advice_card,
    read_last_signal,
    render_advice_card,
    scan_advice_text,
    t2_admission,
    universe_real_codes,
)
from stock_trading_agent.loop.assertor import AdviceContext, assess_result
from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.loop.schemas import ADVICE_SCHEMA
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.queue import QueueState, TaskRecord


def card_payload(**override) -> dict:
    base = {
        "kind": "monitor_review",
        "outcome": "card",
        "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
        "candidate_action": "持有观察",
        "final_signal": "hold",
        "prev_diff": {"prev_final_signal": "none", "note": "首卡"},
        "t1_sell_pct": 85.0,
        "t1_sell_tier": "大部分",
        "verdict_date": market_today(),
        "opinion_data_date": market_today(),
        "replayed_opinions": 3,
        "trend_ref": {
            "band_rows": [{"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0}]
        },
        "rationale": "合成理由",
    }
    base.update(override)
    return base


def _result(structured: dict | None) -> CCResult:
    return CCResult(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output=structured,
        result_line_present=True,
        transcript_path=Path("/dev/null"),
    )


class TestFiveTierConstants:
    def test_actions_pinned_verbatim(self):
        # vibe-stock references/04 持仓者五档（含等回踩变体）逐字——枚举禁自造
        assert ADVICE_ACTIONS == (
            "可加仓",
            "持有",
            "持有·等回踩",
            "持有观察",
            "减仓",
            "离场",
        )

    def test_schema_enum_matches_actions(self):
        enum = ADVICE_SCHEMA["properties"]["candidate_action"]["enum"]
        assert tuple(enum) == ADVICE_ACTIONS


class TestRenderAndScan:
    def test_render_roundtrip_and_scan_clean(self):
        md = render_advice_card(card_payload(), code="600519", date=market_today(), task_id="t1")
        assert scan_advice_text(md) == []
        meta = parse_advice_card(md)
        assert meta["card"] == "monitor-advice/1"
        assert meta["code"] == "600519"
        assert meta["candidate_action"] == "持有观察"
        assert meta["t1_sell_pct"] == 85.0

    def test_leading_zero_code_roundtrip(self):
        # 0 开头 A 股代码（000/002/300 段）：写侧加引号，roundtrip 免 YAML 1.1 八进制误析
        md = render_advice_card(card_payload(), code="002513", date=market_today(), task_id="t1")
        assert 'code: "002513"' in md
        assert parse_advice_card(md, stem="002513")["code"] == "002513"
        assert parse_advice_card(md)["code"] == "002513"

    def test_legacy_unquoted_octal_code_stem_wins(self):
        # 遗留未加引号卡：PyYAML 八进制误析 002513→1355；stem 在场为权威；无 stem 退化 int→str
        legacy = render_advice_card(card_payload(), code="002513", date="d", task_id="t")
        unquoted = legacy.replace('code: "002513"', "code: 002513")
        assert parse_advice_card(unquoted, stem="002513")["code"] == "002513"
        assert parse_advice_card(unquoted)["code"] == "1355"

    def test_corrupt_frontmatter_returns_empty(self):
        # 0.0.35：坏 front-matter 视同无元数据（/panel/advice 列表对坏卡免疫，不再 500）
        corrupt = "---\ncode: [unclosed\n---\nbody"
        assert parse_advice_card(corrupt) == {}
        assert parse_advice_card(corrupt, stem="600519") == {}

    def test_share_unit_negative_assertion(self):
        md = render_advice_card(
            card_payload(rationale="建议先卖 1200 股观察"),
            code="600519",
            date=market_today(),
            task_id="t1",
        )
        reasons = scan_advice_text(md)
        assert any("份额" in r or "数量" in r for r in reasons)

    def test_hand_unit_negative_assertion(self):
        md = render_advice_card(
            card_payload(rationale="可减 3 手"),
            code="600519",
            date=market_today(),
            task_id="t1",
        )
        assert any("脱敏边界" in r for r in scan_advice_text(md))

    def test_missing_notes_refused(self, monkeypatch: pytest.MonkeyPatch):
        import stock_trading_agent.loop.advice as advice_mod

        monkeypatch.setattr(advice_mod, "DISCLAIMER_FOOTER", "")
        md = render_advice_card(card_payload(), code="600519", date="d", task_id="t")
        assert any("免责尾注" in r for r in scan_advice_text(md))


class TestAdviceFamilyChecks:
    def _ctx(self, **kw) -> AdviceContext:
        base = dict(today=market_today(), last_signal=None, signals_read_ok=True)
        base.update(kw)
        return AdviceContext(**base)

    def test_valid_card_passes(self):
        verdict = assess_result(
            _result(card_payload()),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=self._ctx(),
        )
        assert verdict.ok, verdict.reasons

    def test_facts_only_branch(self):
        payload = {
            "kind": "monitor_review",
            "outcome": "facts_only",
            "facts_note": "触发事实：跌破止损线 2.1%，待收盘复核。",
            "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
        }
        verdict = assess_result(
            _result(payload), "monitor_review", schema=ADVICE_SCHEMA, advice_ctx=self._ctx()
        )
        assert verdict.ok, verdict.reasons

    def test_facts_only_smuggling_card_fields_refused(self):
        payload = card_payload(facts_note="x")  # 卡面字段在场 + facts_note
        payload.pop("outcome")
        payload["outcome"] = "facts_only"
        verdict = assess_result(
            _result(payload), "monitor_review", schema=ADVICE_SCHEMA, advice_ctx=self._ctx()
        )
        assert any("走私" in r for r in verdict.reasons)

    def test_stale_verdict_date_refused(self):
        verdict = assess_result(
            _result(card_payload(verdict_date="2020-01-01")),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=self._ctx(),
        )
        assert any("verdict_date" in r for r in verdict.reasons)

    def test_stale_opinions_refused(self):
        verdict = assess_result(
            _result(card_payload(opinion_data_date="2020-01-01")),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=self._ctx(),
        )
        assert any("保鲜" in r for r in verdict.reasons)

    def test_missing_ctx_refused(self):
        verdict = assess_result(_result(card_payload()), "monitor_review", schema=ADVICE_SCHEMA)
        assert any("advice_ctx" in r for r in verdict.reasons)

    def test_anchor3_matrix(self):
        ctx = self._ctx(last_signal="hold")
        # 更激进拒（buy < hold rank）
        v = assess_result(
            _result(card_payload(final_signal="buy")),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=ctx,
        )
        assert any("锚③" in r for r in v.reasons)
        # 等档/更保守过
        for signal in ("hold", "sell", "strong_sell"):
            v = assess_result(
                _result(card_payload(final_signal=signal)),
                "monitor_review",
                schema=ADVICE_SCHEMA,
                advice_ctx=ctx,
            )
            assert v.ok, (signal, v.reasons)
        # 无记录放行
        v = assess_result(
            _result(card_payload(final_signal="strong_buy")),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=self._ctx(last_signal=None),
        )
        assert v.ok, v.reasons

    def test_signals_unreadable_refused(self):
        v = assess_result(
            _result(card_payload()),
            "monitor_review",
            schema=ADVICE_SCHEMA,
            advice_ctx=self._ctx(signals_read_ok=False),
        )
        assert any("宁拒不漏" in r for r in v.reasons)


class TestReadLastSignal:
    def _root(self, tmp_path: Path) -> Path:
        root = tmp_path / "state"
        root.mkdir()
        return root

    def test_absent_file_passes(self, tmp_path: Path):
        signal, ok = read_last_signal(self._root(tmp_path), "600519")
        assert signal is None and ok is True

    def test_latest_entry_for_code(self, tmp_path: Path):
        root = self._root(tmp_path)
        (root / "signals.json").write_text(
            json.dumps(
                [
                    {"code": "600519", "signal": "buy"},
                    {"code": "601919", "signal": "sell"},
                    {"code": "600519", "signal": "hold"},
                ]
            ),
            encoding="utf-8",
        )
        assert read_last_signal(root, "600519") == ("hold", True)

    def test_corrupt_file_refuses(self, tmp_path: Path):
        root = self._root(tmp_path)
        (root / "signals.json").write_text("{bad", encoding="utf-8")
        assert read_last_signal(root, "600519") == (None, False)

    def test_illegal_signal_word_refuses(self, tmp_path: Path):
        root = self._root(tmp_path)
        (root / "signals.json").write_text(
            json.dumps([{"code": "600519", "signal": "梭哈"}]), encoding="utf-8"
        )
        assert read_last_signal(root, "600519") == (None, False)


class TestAdmissionGate:
    def _task(self, code: str = "600519") -> TaskRecord:
        return TaskRecord(task_id="t", dedup_key="k", kind="monitor_review", code=code)

    def test_under_quota_passes_without_universe(self):
        assert t2_admission(self._task(), done_count=4, real_codes=None) is None

    def test_over_quota_real_passes(self):
        assert t2_admission(self._task(), done_count=5, real_codes={"600519"}) is None

    def test_over_quota_non_real_refused(self):
        reason = t2_admission(self._task(), done_count=5, real_codes={"601919"})
        assert reason and "实盘" in reason

    def test_over_quota_universe_unreadable_refused(self):
        reason = t2_admission(self._task(), done_count=5, real_codes=None)
        assert reason and "宁拒不漏" in reason


class TestUniverseAndCount:
    def test_universe_real_codes(self, tmp_layout: StateLayout):
        tmp_layout.monitor_dir.mkdir(parents=True)
        rows = [{"code": "600519", "kind": "real"}, {"code": "00700", "kind": "sim"}]
        tmp_layout.universe_path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
        assert universe_real_codes(tmp_layout) == {"600519"}

    def test_universe_absent_none(self, tmp_layout: StateLayout):
        assert universe_real_codes(tmp_layout) is None

    def test_count_t2_done_by_market_day(self):

        q = QueueState()
        # UTC 2026-11-08 17:00 = 上海 11-09 01:00 → 计 11-09
        q.submit(
            TaskRecord(
                task_id="a",
                dedup_key="k1",
                kind="monitor_review",
                code="600519",
                enqueued_at="2026-11-08T17:00:00+00:00",
            )
        )
        q.transition("a", "running")
        q.transition("a", "done")
        # 上海 11-08 15:00（UTC 07:00）→ 计 11-08
        q.submit(
            TaskRecord(
                task_id="b",
                dedup_key="k2",
                kind="monitor_review",
                code="601919",
                enqueued_at="2026-11-08T07:00:00+00:00",
            )
        )
        q.transition("b", "running")
        q.transition("b", "done")
        # 非当日 / 非 done 不计
        q.submit(
            TaskRecord(
                task_id="c",
                dedup_key="k3",
                kind="diagnose_quick",
                code="600519",
                enqueued_at="2026-11-08T07:00:00+00:00",
            )
        )
        q.transition("c", "running")
        q.transition("c", "done")
        assert count_t2_done(q, today="2026-11-08") == 1
        assert count_t2_done(q, today="2026-11-09") == 1


class TestAdviceContext:
    def test_context_composition(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "s")
        ctx = advice_context(layout, "600519")  # 无 signals.json → 放行
        assert ctx.today == market_today()
        assert ctx.last_signal is None and ctx.signals_read_ok is True


class TestHasReview:
    """已复核反查（0.0.25）：kind+date+code 三态命中，沿 has_ack 同构。"""

    def _lines(self, kind: str, date: str, code: str) -> list[dict]:
        return [{"kind": kind, "payload": {"date": date, "code": code}}]

    def test_hit(self):
        lines = self._lines("advice_reviewed", "2026-11-09", "600519")
        assert has_review(lines, date="2026-11-09", code="600519") is True

    def test_date_mismatch(self):
        lines = self._lines("advice_reviewed", "2026-11-08", "600519")
        assert has_review(lines, date="2026-11-09", code="600519") is False

    def test_kind_mismatch(self):
        lines = self._lines("briefing_ack", "2026-11-09", "600519")
        assert has_review(lines, date="2026-11-09", code="600519") is False

    def test_empty(self):
        assert has_review([], date="2026-11-09", code="600519") is False
