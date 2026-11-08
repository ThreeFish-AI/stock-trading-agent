"""0.0.14 P0：panel 渲染层单测（五段流水线/意见板类名对齐 + 状态 pill）。"""

from __future__ import annotations

from stock_trading_agent.server.panel import _status_pill, render_session_obs


def _session_body() -> dict:
    return {
        "stages": [
            {"stage": 1, "name": "取证据", "state": "done", "note": "n1"},
            {"stage": 2, "name": "证据包", "state": "current", "note": ""},
            {"stage": 3, "name": "出意见", "state": "pending", "note": ""},
            {"stage": 4, "name": "裁决", "state": "pending", "note": ""},
            {"stage": 5, "name": "报告", "state": "pending", "note": ""},
        ],
        "opinions": {
            "default": [{"source": "strategy:均线金叉", "signal": "buy", "confidence": 0.7}]
        },
        "verdict": None,
        "notes": [],
    }


class TestSessionObs:
    def test_step_classes_align_css(self):
        """五段流水线子类须发 .sc/.sl/.ss（CSS 原型类），非 step-dot 族。"""
        html = render_session_obs(_session_body())
        assert 'class="step done"' in html and 'class="step cur"' in html
        assert '<div class="sc">' in html
        assert '<div class="sl">' in html
        assert '<div class="ss num">' in html
        assert "step-dot" not in html

    def test_opinion_sig_dot_and_word(self):
        """意见板信号 span 须带 .sig 前缀 + .d dot + 信号词（五词点接活）。"""
        html = render_session_obs(_session_body())
        assert 'class="sig sig-b"' in html
        assert '<span class="d"></span>' in html
        assert "buy" in html


class TestStatusPill:
    def test_pill_emits_status_class(self):
        assert _status_pill("done") == '<span class="st done">done</span>'
        assert _status_pill("holding") == '<span class="st holding">holding</span>'
