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
        assert '<th scope="col" class="num">时间</th>' in html
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


class TestMdLite:
    """0.0.18：报告 md 结构化渲染状态机（全文本先 _esc）。"""

    def test_headings_table_list_bold(self):
        from stock_trading_agent.server.panel import _md_lite

        md = (
            "# 诊股报告\n\n## 结论\n\n| 指标 | 值 |\n|---|---|\n"
            "| 置信 | 0.7 |\n\n- 复核止损位\n\n**关键**提示\n"
        )
        html = _md_lite(md)
        assert '<h1 class="md">诊股报告</h1>' in html
        assert '<h2 class="md">结论</h2>' in html
        assert "<table" in html and "<td>置信</td>" in html  # 表格行，分隔行被跳过
        assert "<ul><li>复核止损位</li></ul>" in html
        assert "<b>关键</b>提示" in html

    def test_code_fence_and_escaping(self):
        from stock_trading_agent.server.panel import _md_lite

        html = _md_lite("```\n<script>alert(1)</script>\n```\n\n**<b>**")
        assert "&lt;script&gt;" in html  # 全文本转义
        assert "<pre>" in html and "</pre>" in html

    def test_render_report_body_structured(self):
        from stock_trading_agent.server.panel import render_report_body

        html = render_report_body(
            "600519.cn", "2026-11-06.md", "# 诊股报告\n\n本报告不构成投资建议。\n"
        )
        assert '<div class="md">' in html
        assert "诊股报告" in html and "免责段缺失" not in html


class TestSparklineGeneralized:
    def test_aria_label_and_token_stroke(self):
        from stock_trading_agent.server.panel import _sparkline_svg

        svg = _sparkline_svg([1.0, 1.01, 0.99], aria_label="净值走势，最新 0.99")
        assert 'aria-label="净值走势，最新 0.99"' in svg
        assert "stroke:var(--acc)" in svg  # 硬编码色归 token
        assert "#58a6ff" not in svg

    def test_degrades_below_two_points(self):
        from stock_trading_agent.server.panel import _sparkline_svg

        assert "曲线待积累" in _sparkline_svg([1.0], aria_label="x")


class TestBriefingChips:
    def test_action_items_chips_rendered(self):
        from stock_trading_agent.server.panel import render_briefing_fragment

        payload = {
            "date": "2026-11-08",
            "cards": [
                {
                    "code": "600519",
                    "ack": False,
                    "md": "code: 600519\n\n## 行动项\n- [ ] 复核：止损线 9.5 距现价 3.4%\n",
                }
            ],
        }
        html = render_briefing_fragment(payload)
        assert "复核：止损线 9.5 距现价 3.4%" in html
        assert 'class="kchip"' in html


class TestDesignUpgrade024:
    """0.0.24 整体设计升级：双 bug 修复 / 信号表整治 / 通知聚合 / 错误卡 / 折叠族。"""

    def test_navcard_single_point_structure(self):
        """单数据点不再吞 nv-v/nv-chips 开标签（结构断裂修复）。"""
        from stock_trading_agent.server.panel import render_navcard

        html = render_navcard({"equity_history": [{"date": "2026-11-07", "nav": 1.0}]})
        assert '<div class="nv-v num">1.0000</div>' in html
        assert '<div class="nv-chips">' in html
        assert "成立以来" in html and "今日" not in html
        # div 开闭配平
        assert html.count("<div") == html.count("</div>")

    def test_axis_equity_svg_single_style_attr(self):
        """<text> 双 style 属性合并（font-family 恢复生效）+ <2 点空轴骨架。"""
        from stock_trading_agent.server.panel import _axis_equity_svg

        svg = _axis_equity_svg(
            [
                {"date": "2026-11-06", "nav": 1.0},
                {"date": "2026-11-07", "nav": 1.01},
            ]
        )
        assert 'text-anchor="end" style="font-family' not in svg  # 双 style 负断言
        assert "font-family:var(--mono)" in svg
        skeleton = _axis_equity_svg([{"date": "2026-11-06", "nav": 1.0}])
        assert "待积累" in skeleton and 'style="stroke:var(--line)"' in skeleton

    def test_signals_table_norms_and_confidence_format(self):
        from stock_trading_agent.server.panel import render_signals_table

        html = render_signals_table(
            {
                "rows": [
                    {
                        "code": "600519",
                        "signal": "hold",
                        "confidence": 0.5,
                        "to_stop_pct": 2.1,
                        "profile": "default",
                        "date": "2026-11-05",
                    }
                ],
                "names": {},
                "read_ok": True,
            }
        )
        assert "<colgroup>" in html and '<th scope="col">代码</th>' in html
        assert ">0.50</td>" in html  # 置信两位小数
        assert '<th scope="col" class="num">距止损</th>' in html

    def test_signals_table_stop_column_conditional(self):
        """全表无现价源时距止损列整列省去。"""
        from stock_trading_agent.server.panel import render_signals_table

        html = render_signals_table(
            {
                "rows": [
                    {"code": "600519", "signal": "hold", "confidence": 0.5, "profile": "default"}
                ],
                "names": {},
                "read_ok": True,
            }
        )
        assert '<th scope="col">距止损</th>' not in html  # 列省去（注记仍提示无价口径）

    def test_signals_snapshot_breach_first(self):
        """0.0.24 展示层排序：breach → near → 常序 → 无价（组内保登记序）。"""
        from stock_trading_agent.server.panel_data import signals_snapshot

        entries = [
            {
                "code": "600519",
                "signal": "hold",
                "date": "2026-11-03",
                "key_levels": {"stop": 10.0},
            },  # 现价 10.2 → near(+2%)
            {
                "code": "000001",
                "signal": "hold",
                "date": "2026-11-04",
                "key_levels": {"stop": 10.0},
            },  # 现价 9.9 → breach(-1%)
            {
                "code": "00700",
                "signal": "hold",
                "date": "2026-11-05",
                "key_levels": {"stop": 10.0},
            },  # 现价 12.0 → 常序
            {
                "code": "300750",
                "signal": "hold",
                "date": "2026-11-06",
                "key_levels": {"stop": 10.0},
            },  # 无价
        ]
        snap = signals_snapshot(
            entries, {"600519": 10.2, "000001": 9.9, "00700": 12.0, "300750": None}
        )
        codes = [r["code"] for r in snap["rows"]]
        assert codes == ["000001", "600519", "00700", "300750"]  # breach 置顶

    def test_ts_short_cross_day(self):
        """跨日台账 MM-DD HH:MM；断言与运行机时区无关（CI=UTC/本机=+08 均可跑）。"""
        import datetime as dt

        from stock_trading_agent.server.panel import _ts_short

        raw = "2026-11-08T02:50:29+00:00"
        expected = dt.datetime.fromisoformat(raw).astimezone().strftime("%m-%d %H:%M")
        assert _ts_short(raw) == expected  # +00:00 串按运行机时区换算（非纯切片）
        assert _ts_short(raw).startswith("11-08 ")
        assert _ts_short("not-a-ts") == "not-a-ts"  # 解析失败回退

    def test_error_card_escape_and_retry(self):
        from stock_trading_agent.server.panel import render_error_card

        card = render_error_card(404, "报告不存在<script>", retry_path="/api/panel/report/x")
        assert "操作未生效（HTTP 404）" in card
        assert "&lt;script&gt;" in card
        assert "重试" in card and 'hx-get="/api/panel/report/x"' in card
        post_card = render_error_card(422, "无在持仓位")
        assert "重试" not in post_card  # POST 不给重试

    def test_notifications_grouped(self):
        from stock_trading_agent.server.panel import render_notifications

        items = [
            {
                "ts": "2026-11-08T09:31:00+08:00",
                "code": "601899",
                "title": "盯盘通知",
                "body": "紫金矿业 已低于止损线 28.01 (-0.39%)",
                "source": "events",
            },
            {
                "ts": "2026-11-08T10:02:00+08:00",
                "code": "601899",
                "title": "盯盘通知",
                "body": "紫金矿业 已低于止损线 28.01 (-0.29%)",
                "source": "events",
            },
        ]
        html = render_notifications({"items": items})
        assert "×2" in html  # 聚合 chip
        assert "09:31" in html and "10:02" in html  # 首/末时刻
        assert "现在" not in html  # 「·现在」删除
        assert "全部 2 条" in html  # details 展开
        assert 'role="status"' in html and 'role="region"' in html

    def test_briefing_details_fold_ack_outside_summary(self):
        from stock_trading_agent.server.panel import render_briefing_fragment

        html = render_briefing_fragment(
            {
                "date": "2026-11-08",
                "cards": [{"code": "600519", "ack": False, "md": "正文" * 100}],
            }
        )
        assert '<details class="brief-fold">' in html
        assert '<summary class="brief-sum">' in html
        assert "<pre" in html
        # ack 按钮在 summary 外（details toggle 不吞按钮点击）
        assert html.index("确认复核") < html.index("<summary")

    def test_todos_sdot_and_href(self):
        from stock_trading_agent.server.panel import render_todos

        html = render_todos(
            {"todos": [{"source": "pending_fill", "code": "600519", "text": "x", "chip": "待确认"}]}
        )
        assert 'class="cbx"' not in html and "sdot-wait" in html
        assert 'href="#/paper"' in html

    def test_audit_summary_chips_and_head_tag(self):
        from stock_trading_agent.server.panel import render_audit

        lines = [
            {
                "seq": 1,
                "ts": "2026-11-08T02:00:00+00:00",
                "kind": "boot",
                "payload": {},
                "hash": "a" * 64,
            },
            {
                "seq": 2,
                "ts": "2026-11-08T03:00:00+00:00",
                "kind": "l2_toggled",
                "payload": {},
                "hash": "b" * 64,
            },
        ]
        ok_html = render_audit(
            lines, {"tasks": 0, "tokens": {"total": 0}, "cost_usd_equiv": 0}, chain_ok=True
        )
        assert "链校验通过" in ok_html and '<span class="tag">head</span>' in ok_html
        assert ok_html.index("chips") < ok_html.index("<table")  # 摘要在表前
        none_html = render_audit([], {"tasks": 0}, chain_ok=None)
        assert "链校验" not in none_html  # None 不渲染摘要（TestAuditChainRender 既有钉）

    def test_ca_form_structure(self):
        from stock_trading_agent.server.panel import render_ca_form

        html = render_ca_form(has_positions=True)
        assert 'hx-target="#paper-ca-result"' in html and "馈送入账" in html
        assert "hx-confirm=" in html and 'id="paper-ca-result"' in html
        folded = render_ca_form(has_positions=False)
        assert "<details" in folded and "需在持仓" in folded

    def test_reports_list_norms(self):
        from stock_trading_agent.server.panel import render_reports_list

        html = render_reports_list(
            {
                "reports": [
                    {
                        "code": "600519",
                        "market": "cn",
                        "dir": "600519.cn",
                        "latest": "2026-11-06.md",
                        "count": 4,
                    }
                ]
            }
        )
        assert "<colgroup>" in html and '<th scope="col">标的</th>' in html
        assert "4 份在档" in html and 'hx-swap="innerHTML show:top"' in html

    def test_perf_grid_roles(self):
        from stock_trading_agent.server.panel import _perf_grid

        html = _perf_grid({"sharpe": None, "max_drawdown": None, "win_rate": None})
        assert 'role="list"' in html and html.count('role="listitem"') == 3
