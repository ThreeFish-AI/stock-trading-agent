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
        assert (
            '<span id="dg-task-meta" class="cm" hx-swap-oob="true"' in html
            and ">1 项</span>" in html
        )
        # 0.0.36：任务表入 tbl-wrap sm 限高——裸表 20 行可拉至 ~800px，把同列「会话观测」
        # grow 卡挤至 2px 不可见（Issue #36 ②页实测绘面）
        assert '<div class="tbl-wrap sm"><table class="tbl">' in html
        empty = render_tasks_table({"tasks": []})
        assert (
            '<span id="dg-task-meta" class="cm" hx-swap-oob="true"' in empty
            and ">共 0 项</span>" in empty
        )

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
        assert ">09:31</div>" in html  # ISO 串取市场钟面 HH:MM（+08 输入 → 市场钟面不变）
        assert 'title="2026-11-08T09:31:17+08:00"' in html  # 全值 Tooltip
        assert (
            '<span id="ov-ev-count" class="cm" hx-swap-oob="true"' in html
            and ">1 条</span>" in html
        )

    def test_ts_hhmm_market_clock_tz_independent(self):
        """0.0.34 CI 回归：aware 串统一转市场钟面（非系统本地——UTC runner 实证错钟面）。"""
        from stock_trading_agent.server.panel import _ts_hhmm

        # 双钟面场景：+00:00 台账串不得显示 UTC 钟面（13:26），须显示市场钟面 21:26
        assert _ts_hhmm("2026-11-09T13:26:00+00:00") == "21:26"
        # 市场钟面输入原位呈现；朴素串视为已在市场钟面；非 ISO 短串原样
        assert _ts_hhmm("2026-11-09T21:26:00+08:00") == "21:26"
        assert _ts_hhmm("2026-11-09T09:31:17") == "09:31"
        assert _ts_hhmm("09:31") == "09:31"


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
        # 0.0.38.1：说明行上移卡头（index.html cm 承载），表下 tbl-note 零输出节约一行高度
        assert "tbl-note" not in html

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
        # oob span 属性即终态（outerHTML 替换）：class="cm" 必带，否则轮询后丢卡头 chip 样式
        assert (
            '<span id="ov-todo-count" class="cm" hx-swap-oob="true"' in html
            and ">1 项</span>" in html
        )
        empty = render_todos({"todos": []})
        assert '<span id="ov-todo-count" class="cm" hx-swap-oob="true"' in empty
        assert ">共 0 项</span>" in empty

    def test_cap_cm_contract(self):
        """0.0.39 卡头 counter chip：截断「显示 X/Y unit」、未截断回落 uncapped、恒发射 oob。"""
        from stock_trading_agent.server.panel import _cap_cm

        # 截断态
        html = _cap_cm("x-cm", 17, 3, unit="项", title="指引")
        assert 'id="x-cm"' in html and "hx-swap-oob" in html and 'class="cm"' in html
        assert "显示 3/17 项" in html and 'title="指引"' in html
        # 未截断态：uncapped 回落，缺省「共 N unit」
        assert "17 项" in _cap_cm("x-cm", 17, 3, unit="项", uncapped="17 项")
        assert "共 17 项" in _cap_cm("x-cm", 17, 17, unit="项")
        # 空态：共 0 恒复位（防陈旧计数残留）
        assert "共 0 项" in _cap_cm("x-cm", 0, 0, unit="项")
        # capped 覆写 + extra_attrs
        html = _cap_cm("x-cm", 7, 5, capped="重放 5/7 组 · 共 14 条", extra_attrs=' role="status"')
        assert "重放 5/7 组 · 共 14 条" in html and 'role="status"' in html

    def test_topn_cap_renderers(self):
        """0.0.38 topN 收敛（单滚动条治理）：页滚位外区域截断 + counter 行。"""
        from stock_trading_agent.server.panel import (
            render_advice_library,
            render_audit_archive,
            render_calendar,
            render_notifications,
            render_sessions_table,
            render_tasks_table,
            render_todos,
        )

        many_todos = [
            {"source": "pending_fill", "code": "600519", "text": "x", "chip": "待确认"}
            for _ in range(17)
        ]
        html = render_todos({"todos": many_todos})
        assert html.count('<div class="todo">') == 3
        # counter 归卡头 chip（截断态），卡体零独立说明行
        assert 'id="ov-todo-count"' in html and "显示 3/17 项" in html
        assert "tbl-note" not in html

        rows = [{"code": "600519", "date": "2026-11-12", "type": "公告", "detail": "d"}] * 7
        assert render_calendar({"upcoming": rows}).count("kchip") == 3
        assert "显示 3/7 条" in render_calendar({"upcoming": rows})
        assert "共 0 条" in render_calendar({"upcoming": []})  # 空态恒复位

        notif_rows = [
            {
                "source": "watch",
                "code": "600519",
                "ts": "2026-11-12T10:00:00+08:00",
                "title": "t",
                "body": "b",
            }
            for _ in range(8)
        ]
        html = render_notifications({"items": notif_rows})
        # 同 (source, code) 全并入一组 → 1 组卡不截断
        assert html.count('class="card note"') == 1
        notif_rows2 = [
            {
                "source": "watch",
                "code": f"60051{i}",
                "ts": "2026-11-12T10:00:00+08:00",
                "title": "t",
                "body": "b",
            }
            for i in range(7)
        ]
        html = render_notifications({"items": notif_rows2})
        assert html.count('class="card note"') == 5
        assert "重放 5/7 组 · 共 7 条" in html

        tasks = [
            {
                "kind": "diagnosis",
                "code": "600519",
                "status": "done",
                "enqueued_at": "2026-11-12T10:00:00+08:00",
            }
        ] * 9
        html = render_tasks_table({"tasks": tasks})
        assert html.count("</td>") == 6 * 5  # 6 行 × 5 列
        assert "显示 6/9 项" in html

        cards = [
            {
                "code": "600519",
                "candidate_action": "hold",
                "final_signal": "hold",
                "prev_final_signal": "",
                "t1_sell_pct": "10",
                "t1_sell_tier": "mid",
                "reviewed": False,
                "mtime": "2026-11-12T10:00:00+08:00",
            }
            for _ in range(12)
        ]
        html = render_advice_library({"cards": cards, "refused": [], "date": "2026-11-12"})
        assert html.count("</td>") == 10 * 7
        assert "显示 10/12 张 · 只读" in html

        sess = {
            "sessions": {
                f"u{i}": {
                    "ts": f"2026-11-12T10:0{i}:00+08:00",
                    "session_uuid": f"u{i}",
                    "task_id": "t",
                    "usage_tokens": {"total": 1},
                    "cost_usd_equiv": 0.1,
                    "transcript_path": "",
                }
                for i in range(8)
            }
        }
        html = render_sessions_table(sess)
        assert html.count("</td>") == 6 * 6
        assert "显示 6/8 条" in html

        files = [
            {
                "file": f"sess-2026-11-12-t{i}.jsonl",
                "size_bytes": 100,
                "mtime": "2026-11-12T10:00:00+08:00",
            }
            for i in range(6)
        ]
        html = render_audit_archive({"dates": ["2026-11-12"], "date": "2026-11-12", "files": files})
        assert html.count("</td>") == 5 * 2
        assert "显示 5/6 个" in html

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


class TestVerdictStrongVisuals037:
    """②裁决卡强视觉（0.0.37 对齐原型）：降档横幅 / 终判大字 / 分歧格 / 置信帽 track。"""

    @staticmethod
    def _render(verdict: dict) -> str:
        from stock_trading_agent.server.panel import render_session_obs

        return render_session_obs(
            {
                "stages": [{"stage": 4, "name": "裁决", "state": "done"}],
                "opinions": {},
                "verdict": verdict,
            }
        )

    def test_cap_press_attributed_not_riskguard(self):
        # 门4 F-1：核心证据降级 + strong 初判时 RiskGuard no-op、降档来自置信帽压制——
        # 归因须指向置信帽，副文案取首条非 no-op log（不再显示自相矛盾的 no-op）
        html = self._render(
            {
                "initial": "strong_buy",
                "final_before_cap": "strong_buy",
                "final": "buy",
                "risk_guard_log": [
                    "no-op",
                    "confidence_cap strong_buy->buy (core evidence degraded)",
                ],
                "confidence": 0.55,
                "buckets": {},
            }
        )
        assert ">置信帽压制 · strong_buy → buy<" in html
        assert ">confidence_cap strong_buy-&gt;buy (core evidence degraded)<" in html
        assert 'title="裁决链：no-op；confidence_cap' in html  # 全链沉 title

    def test_dual_segment_attribution(self):
        # RiskGuard 降档 + 置信帽压制双段并存：动手段者并列表述
        html = self._render(
            {
                "initial": "strong_buy",
                "final_before_cap": "strong_sell",
                "final": "sell",
                "risk_guard_log": [
                    "veto 强制反转",
                    "confidence_cap strong_sell->sell (core evidence degraded)",
                ],
                "confidence": 0.5,
                "buckets": {},
            }
        )
        assert ">RiskGuard 降档 + 置信帽压制 · strong_buy → sell<" in html
        assert ">veto 强制反转<" in html

    def test_downgrade_banner_and_track(self):
        # 桶键=意见自身信号方向的绝对分桶（vibe-stock 03-synthesis SSOT，实数据对拍）；
        # 标签直译 看多/看空/中性，warn 高亮=与终判不同族且非空（review 门2 F1 语义钉正）
        html = self._render(
            {
                "initial": "buy",
                "final": "hold",
                "risk_guard_log": ["止损击穿机械覆写"],
                "confidence": 0.55,
                "consensus_level": "majority",
                "buckets": {"bullish": ["a", "b"], "bearish": ["c"], "neutral": []},
                "evidence_quality": {"confidence_cap": 0.58},
            }
        )
        assert 'class="dg-tx">RiskGuard 降档 · buy → hold<' in html
        assert "止损击穿机械覆写" in html
        assert 'class="v-word hd">hold<' in html
        assert '<div class="fill" style="width:55%"></div>' in html
        assert '<div class="capmark" style="left:58%"></div>' in html
        # final=hold：看多（buy 族）/看空（sell 族）均与终判异向 → hot；中性同族 + 空桶不高亮
        hot_bull = '<div class="bk hot" title="bullish">'
        assert hot_bull + '<div class="bk-v num">2</div><div class="bk-k">看多<' in html
        hot_bear = '<div class="bk hot" title="bearish">'
        assert hot_bear + '<div class="bk-v num">1</div><div class="bk-k">看空<' in html
        assert (
            '<div class="bk" title="neutral"><div class="bk-v num">0</div><div class="bk-k">中性<'
            in html
        )

    def test_bucket_highlight_follows_final_family(self):
        # final=sell：看空同族不亮；看多/中性异向亮（方向语义随终判翻转，非恒挂 bearish）
        html = self._render(
            {
                "initial": "sell",
                "final": "sell",
                "confidence": 0.6,
                "buckets": {"bullish": ["a"], "bearish": ["b", "c"], "neutral": ["d"]},
            }
        )
        assert '<div class="bk hot" title="bullish">' in html
        assert '<div class="bk hot" title="neutral">' in html
        assert '<div class="bk" title="bearish">' in html
        assert "看空" in html and "分歧" not in html

    def test_bad_evidence_quality_shape_no_crash(self):
        # 外仓坏形态（list）视同缺席不炸页（review 门2 F2 防御守卫）
        html = self._render(
            {
                "initial": "hold",
                "final": "hold",
                "confidence": 0.6,
                "buckets": {},
                "evidence_quality": ["bad", "shape"],
            }
        )
        assert 'class="v-word hd">hold<' in html and "帽" not in html

    def test_no_downgrade_no_banner(self):
        html = self._render(
            {"initial": "hold", "final": "hold", "confidence": 0.6, "buckets": {"bullish": ["a"]}}
        )
        assert "dg-tx" not in html
        assert 'class="v-word hd">hold<' in html

    def test_tier_word_cap_omits_capmark(self):
        # 生产 evidence_quality.confidence_cap 为档位词（high）非数值——fill 恒渲染、帽线省略（F2）
        html = self._render(
            {
                "initial": "hold",
                "final": "hold",
                "confidence": 0.63,
                "buckets": {},
                "evidence_quality": {"confidence_cap": "high"},
            }
        )
        assert '<div class="fill" style="width:63%"></div>' in html
        assert "capmark" not in html
        # 标签恒显帽原值（与 v-meta 同口径），帽线仅数值可解析时渲染
        assert "0.63 / 帽 high" in html

    def test_confidence_unparseable_no_track(self):
        html = self._render({"initial": "buy", "final": "sell", "confidence": "—"})
        assert 'class="track"' not in html and "capbar" not in html
        assert 'class="v-word dn">sell<' in html
