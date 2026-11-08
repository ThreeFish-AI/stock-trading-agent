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


class TestReportFragment:
    def test_body_and_disclaimer_check(self, tmp_path: Path):
        client, layout = _make_client(tmp_path)
        _seed_report(layout)
        frag = client.get("/api/panel/report/600519.cn/2026-11-06.md").text
        assert "诊股报告" in frag and "免责段缺失" not in frag
        listing = client.get("/api/panel/reports").text
        assert "600519" in listing

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
