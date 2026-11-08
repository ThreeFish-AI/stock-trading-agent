"""FastAPI 面板冒烟：只读路由、TrustedHost、静态页 NR-2 横幅、提交契约（422 白名单闸）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_trading_agent.server.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(state_root=tmp_path / "state"))


class TestReadRoutes:
    def test_health(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200 and resp.json()["ok"] is True

    def test_queue_empty_default(self, client: TestClient):
        assert client.get("/api/queue").json()["version"] == 1

    def test_sessions_empty_default(self, client: TestClient):
        assert client.get("/api/sessions").json()["sessions"] == {}

    def test_decisions_empty_default(self, client: TestClient):
        assert client.get("/api/decisions").json() == []

    def test_paper_absent_default(self, client: TestClient):
        body = client.get("/api/paper").json()
        assert body["exists"] is False

    def test_budget_default_off(self, client: TestClient):
        assert client.get("/api/budget").json()["enabled"] is False

    def test_reads_state_from_injected_root(self, tmp_path: Path):
        from stock_trading_agent.state.store import atomic_write_json

        layout_root = tmp_path / "state2"
        atomic_write_json(layout_root / "agent" / "queue.json", {"version": 1, "tasks": []})
        with TestClient(create_app(state_root=layout_root)) as c:
            assert c.get("/api/queue").status_code == 200


class TestSecurity:
    def test_untrusted_host_rejected(self, tmp_path: Path):
        with TestClient(create_app(state_root=tmp_path), base_url="http://evil.example") as c:
            assert c.get("/api/health").status_code == 400

    def test_submit_contract_replaces_501(self, client: TestClient):
        # 501 占位已实现（M1 接线）：空 body → pydantic 422（kind/code 白名单闸）
        assert client.post("/api/queue/submit", json={}).status_code == 422
        assert (
            client.post("/api/queue/submit", json={"kind": "hft", "code": "600519"}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/queue/submit", json={"kind": "diagnose_quick", "code": "6005191"}
            ).status_code
            == 422
        )

    def test_monitor_projection_empty_default(self, client: TestClient):
        events = client.get("/api/monitor/events").json()
        assert events["events"] == [] and events["count"] == 0
        status = client.get("/api/monitor/status").json()
        assert status["loop"]["active"] is False  # 无 lifespan → runtime 未启动（只读默认）
        assert status["slots"]["outlook"]["done"] is False
        assert client.get("/api/reports").json() == {"reports": []}

    def test_panel_fragments_serve_html(self, client: TestClient):
        for path in ("/api/panel/overview", "/api/panel/events", "/api/panel/audit"):
            resp = client.get(path)
            assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]

    def test_panel_vendor_htmx_served(self, client: TestClient):
        resp = client.get("/vendor/htmx.min.js")
        assert resp.status_code == 200 and len(resp.content) > 10000

    def test_decisions_tail_clamped(self, client: TestClient):
        assert client.get("/api/decisions?tail=0").status_code == 422  # 下界
        assert client.get("/api/decisions?tail=-3").status_code == 422  # 负值
        assert client.get("/api/decisions?tail=10000").status_code == 422  # 上界
        assert client.get("/api/decisions?tail=50").status_code == 200


class TestStatic:
    def test_index_serves_with_nr2_banner(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "NR-2" in resp.text
        assert "不构成投资建议" in resp.text  # 免责四件套文字位
        assert "永不接真实下单" in resp.text
