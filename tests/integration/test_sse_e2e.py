"""PR-E 端到端：SSE 流首事件、briefing 全链（卡落盘→面板→ack）、stats 端点、vendor 锚。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout


@pytest.fixture
def app(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    runtime = LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    return create_app(state_root=layout.root, runtime=runtime)


class TestSSEStream:
    def test_stream_yields_retry_then_close(self, app):
        """短生命周期冒烟：读首事件（retry/心跳）即断开（防长连挂测试）。"""
        with TestClient(app) as client:
            with client.stream("GET", "/api/stream") as resp:
                assert resp.status_code == 200
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if b"retry" in body:
                        break
                assert b"retry" in body

    def test_index_has_sse_wiring(self, app):
        with TestClient(app) as client:
            html = client.get("/").text
            assert 'hx-ext="sse"' in html and 'sse-connect="/api/stream"' in html
            assert "sse:paper" in html and "sse:decisions" in html  # compound triggers

    def test_vendor_sse_served(self, app):
        with TestClient(app) as client:
            resp = client.get("/vendor/sse.js")
            assert resp.status_code == 200 and len(resp.content) > 1000


class TestBriefingChain:
    def test_full_chain_card_and_ack(self, tmp_path: Path, app):
        with TestClient(app) as client:
            runtime = client.app.state.loop_runtime
            runtime.submit(kind="briefing_review", code="600519")
            task = runtime.consume_one()
            assert task is not None and task.status == "done"

            layout_root = Path(client.app.state.state_root)
            day_dir = layout_root / "agent" / "briefing"
            cards = list(day_dir.rglob("600519.md"))
            assert cards, "briefing 卡应落盘"
            md = cards[0].read_text(encoding="utf-8")
            assert "不构成投资建议" in md and "复核" in md

            payload = client.get("/api/panel/briefing").text
            assert "600519" in payload and "待确认" in payload

            # ack 需 daemon 持有者（降级态 503）——本 fixture runtime 未 start → 503 先验
            resp = client.post(
                "/api/panel/briefing/ack", data={"date": "2026-11-08", "code": "600519"}
            )
            assert resp.status_code == 503

    def test_ack_with_active_runtime(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        runtime = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
        app = create_app(state_root=layout.root, runtime=runtime)
        with TestClient(app) as client:
            assert runtime.start()  # is_active 需 worker 线程（ack 端点门槛）
            runtime.submit(kind="briefing_review", code="600519")
            runtime.consume_one()
            from stock_trading_agent.loop.monitor_events import market_today

            resp = client.post(
                "/api/panel/briefing/ack",
                data={"date": market_today(), "code": "600519"},
            )
            assert resp.status_code == 200
            assert "已确认" in resp.text
            decisions = client.get("/api/decisions").json()
            assert any(d["kind"] == "briefing_ack" for d in decisions)
            runtime.stop()

    def test_bad_date_422(self, app):
        with TestClient(app) as client:
            assert client.app.state.loop_runtime.start()
            resp = client.post(
                "/api/panel/briefing/ack", data={"date": "2020-01-01", "code": "600519"}
            )
            assert resp.status_code == 422


class TestStatsEndpoint:
    def test_stats_seeded(self, app):
        with TestClient(app) as client:
            runtime = client.app.state.loop_runtime
            runtime.submit(kind="briefing_review", code="600519")
            runtime.consume_one()
            body = client.get("/api/stats").json()
            assert body["decisions"]["done"] >= 1
            assert "budget_curve" in body
            frag = client.get("/api/panel/stats").text
            assert "任务画像" in frag and "假设性" in frag
