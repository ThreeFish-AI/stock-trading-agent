"""FastAPI 面板冒烟：只读路由、TrustedHost、静态页 NR-2 横幅、POST 501。"""

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

    def test_submit_is_501_placeholder(self, client: TestClient):
        assert client.post("/api/queue/submit", json={}).status_code == 501

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
