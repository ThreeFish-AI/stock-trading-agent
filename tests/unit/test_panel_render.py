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


class TestAuditChainRender:
    def _lines(self):
        return [
            {
                "seq": 1,
                "ts": "2026-11-08T08:00:00+08:00",
                "kind": "task_submitted",
                "payload": {"kind": "diagnose_quick"},
                "hash": "a" * 12,
            },
        ]

    def test_chain_ok_note(self):
        from stock_trading_agent.server.panel import render_audit

        html = render_audit(self._lines(), {}, chain_ok=True)
        assert "链校验通过" in html and "序号" in html and "hash 链" in html
        assert '<th scope="col">类型</th>' in html and "<colgroup>" in html

    def test_chain_tampered_warns(self):
        from stock_trading_agent.server.panel import render_audit

        html = render_audit(self._lines(), {}, chain_ok=False)
        assert "校验失败" in html and 'class="warn"' in html

    def test_chain_unknown_renders_without_note(self):
        from stock_trading_agent.server.panel import render_audit

        html = render_audit(self._lines(), {}, chain_ok=None)
        assert "链校验" not in html


class TestPerfGrid:
    def test_perf_grid_full_and_empty(self):
        from stock_trading_agent.server.panel import _perf_grid

        full = _perf_grid({"sharpe": 1.23, "max_drawdown": -0.05, "win_rate": None})
        assert "1.23" in full and "-5.0%" in full and "—" in full  # 胜率 None 显 —
        empty = _perf_grid({"sharpe": None, "max_drawdown": None, "win_rate": None})
        assert empty.count("—") >= 3


class TestDiagForm:
    def test_row_labels_no_inline_style(self):
        """0.0.16：行式 label（for/id 成对 + title Tooltip）+ 零 inline style。"""
        from stock_trading_agent.server.panel import render_diag_form

        html = render_diag_form()
        assert '<label for="dg-code"' in html and '<label for="dg-kind"' in html
        assert 'id="dg-code" name="code"' in html and 'id="dg-kind" name="kind"' in html
        assert "style=" not in html
        assert 'hx-disabled-elt="find button"' in html


class TestTableNorms:
    """0.0.15 表格规范：中文列头 + colgroup + scope + 空值 —。"""

    def test_events_table_norms(self):
        from stock_trading_agent.server.panel import render_events_table

        html = render_events_table(
            {
                "events": [
                    {
                        "ts": "2026-11-08T09:31:00+08:00",
                        "code": "600519",
                        "rule": "stop_break",
                        "level": "T0",
                        "review": "",
                        "suppress_reason": "",
                    },
                ]
            }
        )
        assert '<th scope="col">时间</th>' in html
        assert "<colgroup>" in html
        assert ">—</td>" in html  # 空值占位

    def test_tasks_table_oob_meta(self):
        from stock_trading_agent.server.panel import render_tasks_table

        html = render_tasks_table(
            {
                "tasks": [
                    {
                        "kind": "monitor_review",
                        "code": "600519",
                        "status": "done",
                        "enqueued_at": "t",
                    }
                ]
            }
        )
        assert '<span id="dg-task-meta" hx-swap-oob="true">1 项</span>' in html
        empty = render_tasks_table({"tasks": []})
        assert '<span id="dg-task-meta" hx-swap-oob="true">0 项</span>' in empty

    def test_event_feed_hhmm_and_oob_count(self):
        from stock_trading_agent.server.panel import render_event_feed

        html = render_event_feed(
            {
                "events": [
                    {
                        "ts": "2026-11-08T09:31:17+08:00",
                        "code": "600519",
                        "rule": "stop_break",
                        "level": "T0",
                    },
                ]
            }
        )
        assert ">09:31</div>" in html  # ISO 串取 HH:MM
        assert 'title="2026-11-08T09:31:17+08:00"' in html  # 全值 Tooltip
        assert '<span id="ov-ev-count" hx-swap-oob="true">1 条</span>' in html
