"""PR-I 面板新端点 e2e：signals/todos/report 路径安全/advice rendered/facts 透出/七页骨架。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper.model import Position, SimPortfolio, Tranche
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout


def _make_client(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    rt = LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    app = create_app(state_root=layout.root, runtime=rt)
    return TestClient(app, headers=ORIGIN_HEADERS), layout


def _seed_signals(layout: StateLayout) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    (layout.root / "signals.json").write_text(
        json.dumps(
            [
                {
                    "code": "600519",
                    "signal": "buy",
                    "confidence": 0.62,
                    "key_levels": {"stop": 9.5},
                    "profile": "default",
                    "date": "2026-11-06",
                },
            ]
        ),
        encoding="utf-8",
    )


def _seed_paper(layout: StateLayout) -> None:
    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf.positions.append(
        Position(
            code="600519",
            market="A",
            lots=2,
            avg_cost=10.0,
            status="holding",
            stop=9.0,
            entry_ref=10.0,
            target1=12.0,
            tranches=[
                Tranche(leg="T2_limit", lots=2, ref_price=10.0, status="filled", filled_price=10.0)
            ],
            last_price=10.5,
            last_price_date="2026-11-06",
        )
    )
    paper_ledger.persist(pf, layout.sim_portfolio_path)


def _seed_events(layout: StateLayout) -> None:
    from stock_trading_agent.loop.monitor_events import market_today

    layout.monitor_events_dir.mkdir(parents=True, exist_ok=True)
    layout.monitor_events_file(market_today()).write_text(
        json.dumps(
            {
                "ts": "09:31:00",
                "code": "600519",
                "market": "cn",
                "kind": "real",
                "rule": "stop_break",
                "level": "T0",
                "notify": True,
                "mech_risk": {"downgrade": True, "severity": "high"},
                "facts": {"price": 9.8, "level": 9.5},
                "review": "止损复核",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_report(layout: StateLayout) -> None:
    d = layout.reports_dir / "600519.cn"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-11-06.md").write_text(
        "# 诊股报告\n\n内容示例。本报告不构成投资建议。\n", encoding="utf-8"
    )


def _seed_advice(layout: StateLayout) -> None:
    from stock_trading_agent.loop.advice import render_advice_card
    from stock_trading_agent.loop.monitor_events import market_today

    day = market_today()
    path = layout.advice_card_path(day, "600519")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "outcome": "card",
        "candidate_action": "持有观察",
        "final_signal": "hold",
        "prev_diff": {"prev_final_signal": "none", "note": "n"},
        "t1_sell_pct": 85.0,
        "t1_sell_tier": "大部分",
        "verdict_date": day,
        "opinion_data_date": day,
        "replayed_opinions": 2,
        "trend_ref": {
            "band_rows": [
                {"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0},
            ]
        },
        "rationale": "r",
    }
    path.write_text(
        render_advice_card(payload, code="600519", date=day, task_id="t"), encoding="utf-8"
    )


class TestSignalsAndTodos:
    def test_empty_defaults(self, tmp_path: Path):
        client, _ = _make_client(tmp_path)
        assert client.get("/api/signals").json()["rows"] == []
        assert client.get("/api/todos").json()["todos"] == []

    def test_seeded(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_signals(layout)
        _seed_paper(layout)
        _seed_events(layout)
        body = client.get("/api/signals").json()
        assert body["rows"][0]["signal"] == "buy"
        assert body["rows"][0]["price"] == 10.5  # sim last_price 优先
        assert body["rows"][0]["to_stop_pct"] == round(10.5 / 9.5 * 100 - 100, 2)
        todos = client.get("/api/todos").json()["todos"]
        assert any(t["source"] == "mech_risk" for t in todos)
        frag = client.get("/api/panel/signals").text  # 0.0.14：五词点 dot 接活
        assert 'class="sig sig-b"' in frag and '<span class="d"></span>' in frag

    def test_facts_passthrough(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_events(layout)
        events = client.get("/api/monitor/events").json()["events"]
        assert events[0]["facts"]["price"] == 9.8  # 0.0.11 透出


class TestAdviceRendered:
    def test_rendered_original_value_and_disk_stays_redacted(self, tmp_path: Path):
        from stock_trading_agent.loop.advice import scan_advice_text
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        _seed_advice(layout)
        _seed_paper(layout)
        day = market_today()
        body = client.get(f"/api/advice/{day}/600519").json()
        assert body["rendered"]["t1_sell_shares"] == 200  # 2 手 × 100/手 原值（呈现态）
        assert scan_advice_text(body["md"]) == []  # 落盘 md 维持脱敏（负断言照过）
        frag = client.get(f"/api/panel/advice-detail/{day}/600519").text
        assert "200" in frag and "持有观察" in frag and "1d" in frag  # 四块+σ√h 表

    def test_rendered_none_without_position(self, tmp_path: Path):
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        _seed_advice(layout)  # 无 paper 持仓
        body = client.get(f"/api/advice/{market_today()}/600519").json()
        assert body["rendered"] is None

    def test_library_code_links_to_detail(self, tmp_path: Path):
        """0.0.15：⑤卡表 code 列改链接接通 advice-detail（此前端点+容器双悬空）。"""
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        _seed_advice(layout)
        day = market_today()
        frag = client.get("/api/panel/advice").text
        assert f'hx-get="/api/panel/advice-detail/{day}/600519"' in frag
        assert 'hx-target="#advice-detail-2"' in frag

    def test_refused_reasons_fallback(self, tmp_path: Path):
        """0.0.15：拒卡通报 reasons 数组回退 + 市场钟面短格式 + 来源列头。"""
        from dataclasses import asdict

        from stock_trading_agent.loop.monitor_events import market_today
        from stock_trading_agent.state.decisions import DecisionLog

        client, layout = _make_client(tmp_path)
        layout.agent_dir.mkdir(parents=True, exist_ok=True)
        log = DecisionLog()
        log.append(
            "2026-11-08T02:50:29+00:00",
            "advice_refused",
            {
                "code": "600519",
                "origin": "assert",
                "reason_code": "schema_or_redline",
                "reasons": ["signal 枚举越界"],
            },
        )
        with open(layout.decisions_path, "w", encoding="utf-8") as f:
            for line in log.lines:
                f.write(json.dumps(asdict(line), ensure_ascii=False) + "\n")
        frag = client.get("/api/panel/advice?date=" + market_today()).text
        assert "signal 枚举越界" in frag  # reasons 回退拼接进通报列
        assert '<th scope="col">来源</th>' in frag
        assert market_today() in frag


class TestReportFragment:
    def test_body_and_disclaimer_check(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_report(layout)
        frag = client.get("/api/panel/report/600519.cn/2026-11-06.md").text
        assert "诊股报告" in frag and "免责段缺失" not in frag
        listing = client.get("/api/panel/reports").text
        assert "600519" in listing

    def test_index_md_served(self, tmp_path: Path):
        """vibe-stock reports 目录含 index.md 索引页——0.0.14 前白名单外全 404。"""
        client, layout = _make_client(tmp_path)
        d = layout.reports_dir / "600519.cn"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.md").write_text(
            "# 诊股报告索引\n\n历史报告列表。本报告不构成投资建议。\n", encoding="utf-8"
        )
        resp = client.get("/api/panel/report/600519.cn/index.md")
        assert resp.status_code == 200
        assert "诊股报告索引" in resp.text

    def test_path_traversal_404(self, tmp_path: Path):
        client, _ = _make_client(tmp_path)
        for bad in [
            "/api/panel/report/../2026-11-06.md",
            "/api/panel/report/600519.cn/../../config.json",
            "/api/panel/report/bad%2Fdir/2026-11-06.md",
            "/api/panel/report/600519.cn/notes.txt",
        ]:
            assert client.get(bad).status_code == 404, bad


class TestSevenPageSkeleton:
    def test_index_has_tabs_pages_sse(self, tmp_path: Path):
        client, _ = _make_client(tmp_path)
        html = client.get("/").text
        for page in ["overview", "diagnosis", "monitor", "paper", "advice", "stats", "audit"]:
            assert f'data-page="{page}"' in html and f'id="page-{page}"' in html
        assert 'sse-connect="/api/stream"' in html and "NR-2" in html
        assert "不构成投资建议" in html
        for frag_path in [
            "/api/panel/phase",
            "/api/panel/topbar",
            "/api/panel/signals",
            "/api/panel/todos",
            "/api/panel/navcard",
            "/api/panel/syscard",
            "/api/panel/overview",
            "/api/panel/mometa",
            "/api/panel/dgmeta",
            "/api/panel/aumeta",
            "/api/panel/dgform",
        ]:
            resp = client.get(frag_path)
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"], frag_path


class TestAuditChain:
    """0.0.15：⑦链校验呈现（load 对断链不抛异常，读侧仍渲染 + 警示）。"""

    def _write_chain(self, layout: StateLayout, tamper: bool) -> None:
        from dataclasses import asdict

        from stock_trading_agent.state.decisions import DecisionLog

        log = DecisionLog()
        log.append("2026-11-08T08:00:00+08:00", "task_submitted", {"kind": "diagnose_quick"})
        log.append("2026-11-08T08:01:00+08:00", "task_done", {"task_id": "t1"})
        if tamper:
            log.lines[-1].payload["task_id"] = "tampered"  # 落盘前改 payload → hash 复算不符
        with open(layout.decisions_path, "w", encoding="utf-8") as f:
            for line in log.lines:
                f.write(json.dumps(asdict(line), ensure_ascii=False) + "\n")

    def test_chain_ok_and_tampered(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        layout.agent_dir.mkdir(parents=True, exist_ok=True)
        self._write_chain(layout, tamper=False)
        frag = client.get("/api/panel/audit").text
        assert "链校验通过" in frag and "hash 链" in frag
        self._write_chain(layout, tamper=True)
        resp = client.get("/api/panel/audit")
        assert resp.status_code == 200  # 断链不拒渲（读侧防线语义）
        assert "校验失败" in resp.text


class TestStatsT1Review:
    def test_t1_review_rendered(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        layout.root.mkdir(parents=True, exist_ok=True)
        (layout.root / "ledger.agent.json").write_text(
            json.dumps({"attribution": {"600519": {"wins": 2, "losses": 1}}}),
            encoding="utf-8",
        )
        body = client.get("/api/stats").json()
        assert body["t1_review"]["sampled"] == 3
        assert body["t1_review"]["rate"] is None  # <30 insufficient
        frag = client.get("/api/panel/stats").text
        assert "T+1 复核命中率" in frag and "只观察不记分" in frag
        assert "任务画像" in frag and "假设性" in frag  # 钉死断言回归


class TestPRDPolish:
    """0.0.18：报告结构化渲染 / ⑥双 sparkline / 壳层 color-scheme 与响应式。"""

    def test_report_body_structured(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_report(layout)
        frag = client.get("/api/panel/report/600519.cn/2026-11-06.md").text
        assert '<div class="md">' in frag and '<h1 class="md">诊股报告</h1>' in frag
        assert "免责段缺失" not in frag

    def test_stats_dual_sparkline_when_curve(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        layout.agent_dir.mkdir(parents=True, exist_ok=True)
        sessions = {
            "sessions": {
                f"s{i}": {
                    "ts": f"2026-11-0{i + 1}T08:00:00+08:00",
                    "usage_tokens": {"input": 100},
                    "cost_usd_equiv": 0.1,
                }
                for i in range(1, 4)
            }
        }
        (layout.agent_dir / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
        frag = client.get("/api/panel/stats").text
        assert frag.count("<svg") >= 2  # tokens + USD 双曲线
        assert 'aria-label="token 日桶曲线"' in frag

    def test_shell_polish_bits(self, tmp_path: Path):
        client, _ = _make_client(tmp_path)
        html = client.get("/").text
        assert '<meta name="color-scheme" content="dark">' in html
        assert "@media (max-width:1180px)" in html and "@media (max-width:880px)" in html
        assert 'class="grid-2"' in html  # ②页 inline grid 迁类
        assert html.count('class="htmx-indicator spin"') == 7  # ①页试点
        assert "animation:rot" in html and "@keyframes rot{" in html  # .spin 修活
        # 死码清除负断言
        for dead in [".notif{", ".ghostbtn", ".dawn{", "tr.sel", ".rpt-r{"]:
            assert dead not in html, dead


class TestHtmxWiring:
    """0.0.14 P0 回归：htmx 生命周期修复（swap 策略/target 落点/表格类对齐/状态色）。"""

    def test_events_review_button_target(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_events(layout)
        frag = client.get("/api/panel/events").text
        assert 'hx-target="#advice-detail"' in frag
        assert "盯盘复核 600519" in frag  # 按钮文案逐字保留（钉死断言）
        assert 'class="tbl"' in frag and 'class="grid"' not in frag

    def test_paper_confirm_buttons_innerhtml(self, tmp_path: Path):
        from stock_trading_agent.paper import pending as paper_pending
        from stock_trading_agent.paper.confirm import PendingFillRequest
        from stock_trading_agent.paper.fills import Bar

        client, layout = _make_client(tmp_path)
        _seed_paper(layout)
        queue = paper_pending.load(layout.sim_pending_fills_path)
        queue.enqueue(
            PendingFillRequest(
                request_id="rid-1",
                code="600519",
                tranche_leg="T2_limit",
                side="buy",
                lots=1,
                ref_price=10.0,
                bar=Bar(date="2026-11-06", open=10.0, high=10.2, low=9.9, close=9.98),
                ts="2026-11-06T09:31:00Z",
            )
        )
        paper_pending.save(queue, layout.sim_pending_fills_path)
        frag = client.get("/api/panel/paper").text
        assert frag.count('hx-target="#paper" hx-swap="innerHTML"') == 3  # 0.0.20 +馈送表单

    def test_briefing_ack_target(self, tmp_path: Path):
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        card = layout.briefing_card_path(market_today(), "600519")
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("- [ ] 复核：止损线复核\n", encoding="utf-8")
        frag = client.get("/api/panel/briefing").text
        assert 'hx-target="#ov-brief"' in frag and 'hx-swap="innerHTML"' in frag

    def test_l2_fragment_and_index_shell_wiring(self, tmp_path: Path):
        client, _ = _make_client(tmp_path)
        frag = client.get("/api/panel/l2").text
        assert 'hx-target="#l2-settings" hx-swap="innerHTML"' in frag
        html = client.get("/").text
        assert (
            'id="ov-events" hx-get="/api/panel/overview" hx-trigger="load, every 180s,'
            ' sse:monitor, sse:multi" hx-swap="innerHTML"' in html
        )
        assert (
            'id="l2-settings" hx-get="/api/panel/l2" hx-trigger="load, every 180s,'
            ' sse:decisions, sse:multi" hx-swap="innerHTML"' in html
        )
        assert ".st.done" in html  # 状态 pill 色规则随壳下发

    def test_shell_interaction_and_a11y(self, tmp_path: Path):
        """0.0.16：load 首绘 / 全局错误条 / tablist a11y / :focus-visible / --t4 AA。"""
        client, _ = _make_client(tmp_path)
        html = client.get("/").text
        # 全部轮询容器首绘（0.0.16 增⑦会话登记卡）
        assert html.count('hx-trigger="load, every 180s') == 21
        assert 'id="global-err" class="riskchip" role="status" aria-live="polite" hidden' in html
        assert "htmx:responseError" in html and "htmx:sendError" in html
        assert 'role="tablist"' in html and html.count('role="tab"') == 7
        assert "aria-selected" in html  # activate() 同步
        assert ":focus-visible" in html
        assert "--t4:#7E8AA5" in html  # 对比度 token 提亮（四底过 AA）
        assert "scrollTo(0,0)" in html

    def test_dgform_row_labels(self, tmp_path: Path):
        """0.0.16：②表单行式（label 同行 for 关联 + Tooltip + 无 inline style + 防重）。"""
        client, _ = _make_client(tmp_path)
        frag = client.get("/api/panel/dgform").text
        assert '<label for="dg-code"' in frag and '<label for="dg-kind"' in frag
        assert 'id="dg-code"' in frag and 'id="dg-kind"' in frag
        assert "证券代码" in frag and "诊股模式" in frag
        assert 'hx-disabled-elt="find button"' in frag
        assert "style=" not in frag
        assert 'pattern="[0-9]{5,6}"' in frag and "required" in frag  # POST 契约不动


class TestP2Endpoints:
    def test_calendar_seeded_from_t1(self, tmp_path: Path):
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        day = market_today()
        wd = layout.t1_workdir(day, "600519")
        wd.mkdir(parents=True)
        (wd / "evidence.json").write_text(
            json.dumps(
                {
                    "meta": {"code": "600519"},
                    "blocks": {
                        "unlock": {
                            "upcoming": [{"date": "2099-01-01", "ratio_pct": 3.3, "type": "定增"}]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        body = client.get("/api/calendar").json()
        assert body["upcoming"] and body["upcoming"][0]["code"] == "600519"
        frag = client.get("/api/panel/calendar").text
        assert "600519" in frag

    def test_notifications_from_events_and_log(self, tmp_path: Path):
        from stock_trading_agent.loop.monitor_events import market_today

        client, layout = _make_client(tmp_path)
        layout.monitor_events_dir.mkdir(parents=True)
        layout.monitor_events_file(market_today()).write_text(
            json.dumps(
                {
                    "ts": "09:31",
                    "code": "600519",
                    "market": "cn",
                    "kind": "real",
                    "rule": "stop_break",
                    "level": "T0",
                    "notify": True,
                    "review": "止损复核",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        logs = layout.monitor_dir / "logs"
        logs.mkdir(parents=True)
        (logs / "watch.out.log").write_text(
            "notify: [vibe-stock 盯盘] 紫金矿业 现价 27.92 ·止损复核 ·待收盘复核·非投资建议\n",
            encoding="utf-8",
        )
        rows = client.get("/api/notifications").json()
        assert any(r["source"] == "log" and "紫金矿业" in r["body"] for r in rows)
        assert any(r["source"] == "events" for r in rows)
        frag = client.get("/api/panel/notifications").text
        assert "紫金矿业" in frag
        assert "禁第三方推送（红线 7）" in frag

    def test_signals_endpoint_has_names(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        layout.root.mkdir(parents=True, exist_ok=True)
        (layout.root / "names.json").write_text(
            json.dumps({"cn:600519": "贵州茅台"}), encoding="utf-8"
        )
        body = client.get("/api/signals").json()
        assert body["names"]["600519"] == "贵州茅台"
        frag = client.get("/api/panel/signals").text
        assert "贵州茅台" in frag or "无在册信号" in frag
