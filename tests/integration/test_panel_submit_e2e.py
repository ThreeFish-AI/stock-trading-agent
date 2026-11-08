"""面板提交半边端到端：POST → 入队 → 消费 → 断言器 → 落盘全链（tmp 状态目录，零网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_trading_agent.server.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    from stock_trading_agent.cc_client.dryrun import DryRunCCClient
    from stock_trading_agent.loop.t1 import T1Runner
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout(root=tmp_path / "state")
    runtime = LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    return create_app(state_root=layout.root, runtime=runtime)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def _runtime(client: TestClient):
    return client.app.state.loop_runtime


class TestSubmitContract:
    def test_submit_201_enqueues_and_persists(self, client: TestClient):
        resp = client.post("/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"})
        assert resp.status_code == 201
        task = resp.json()
        assert task["status"] == "queued" and task["kind"] == "diagnose_quick"
        q = client.get("/api/queue").json()
        assert q["tasks"] and q["tasks"][0]["task_id"] == task["task_id"]
        decisions = client.get("/api/decisions").json()
        assert decisions[-1]["kind"] == "task_submitted"

    def test_duplicate_returns_409(self, client: TestClient):
        body = {"kind": "diagnose_quick", "code": "600519"}
        assert client.post("/api/queue/submit", json=body).status_code == 201
        resp = client.post("/api/queue/submit", json=body)
        assert resp.status_code == 409 and "幂等" in resp.json()["detail"]

    def test_lock_present_returns_423(self, client: TestClient):
        from conftest import hold_lock

        layout_root = Path(client.app.state.state_root)
        (layout_root / "agent").mkdir(parents=True, exist_ok=True)
        (layout_root / "fetch_state.lock").touch()  # 陈旧锁文件：不误判（本机实测坑）
        assert (
            client.post(
                "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"}
            ).status_code
            == 201
        )
        with hold_lock(layout_root / "fetch_state.lock"):  # 真实持锁：423
            resp = client.post(
                "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"}
            )
            assert resp.status_code == 423 and "fetch_state.lock" in resp.json()["detail"]

    def test_watch_lock_held_returns_423(self, app, tmp_path: Path):
        # watch.lock 真实持有（flock）覆盖 LOCK 拒绝路径（WINDOW 判据与 selftest window_lock 同源）
        from conftest import hold_lock

        layout_root = Path(app.state.state_root)
        (layout_root / "monitor").mkdir(parents=True, exist_ok=True)
        with hold_lock(layout_root / "monitor" / "watch.lock"):
            with TestClient(app) as c:
                resp = c.post(
                    "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"}
                )
                assert resp.status_code == 423 and "watch.lock" in resp.json()["detail"]

    def test_bad_kind_and_code_422(self, client: TestClient):
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
        assert (
            client.post(
                "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519", "x": 1}
            ).status_code
            == 422
        )


class TestPanelFlow:
    def test_full_chain_submit_consume_done(self, client: TestClient):
        assert (
            client.post(
                "/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"}
            ).status_code
            == 201
        )
        task = _runtime(client).consume_one()
        assert task is not None and task.status == "done"
        q = client.get("/api/queue").json()
        assert q["tasks"][0]["status"] == "done"
        assert client.get("/api/sessions").json()["sessions"]
        decisions = client.get("/api/decisions").json()
        assert decisions[-1]["kind"] == "task_done"
        budget = client.get("/api/budget").json()
        assert budget["tasks"] == 1

    def test_panel_submit_fragment_html(self, client: TestClient):
        resp = client.post("/api/panel/submit", data={"kind": "monitor_review", "code": "600519"})
        assert resp.status_code == 201
        assert "text/html" in resp.headers["content-type"]
        assert "monitor_review" in resp.text and 'hx-trigger="every 5s"' in resp.text

    def test_panel_submit_reject_fragment(self, client: TestClient):
        resp = client.post("/api/panel/submit", data={"kind": "nope", "code": "600519"})
        assert resp.status_code == 422 and "task-card" in resp.text

    def test_task_fragment_terminal_stops_polling(self, client: TestClient):
        client.post("/api/queue/submit", json={"kind": "diagnose_quick", "code": "600519"})
        task_id = client.get("/api/queue").json()["tasks"][0]["task_id"]
        _runtime(client).consume_one()
        resp = client.get(f"/api/panel/task/{task_id}")
        assert resp.status_code == 200
        assert "hx-trigger" not in resp.text  # 终态停轮询
        assert "done" in resp.text


class TestMonitorProjection:
    def test_events_endpoint_reads_ledger(self, client: TestClient):
        from stock_trading_agent.loop.monitor_events import market_today
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=Path(client.app.state.state_root))
        f = layout.monitor_events_file(market_today())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            json.dumps(
                {
                    "ts": "09:31:00",
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
            + "\n{坏行\n",
            encoding="utf-8",
        )
        body = client.get("/api/monitor/events").json()
        assert body["count"] == 1 and body["skipped"] == 1
        assert body["events"][0]["review"] == "止损复核"
        frag = client.get("/api/panel/events").text
        assert "止损复核" in frag and "盯盘复核 600519" in frag

    def test_status_slots_and_reports(self, client: TestClient, tmp_path: Path):
        layout_root = Path(client.app.state.state_root)
        (layout_root / "monitor").mkdir(parents=True, exist_ok=True)
        (layout_root / "monitor" / "state.json").write_text(
            json.dumps({"date": "x", "close_done": True}), encoding="utf-8"
        )
        status = client.get("/api/monitor/status").json()
        assert status["slots"]["monitor"]["done"] is True
        assert status["locks"]["watch"] is False

        reports_root = layout_root / "reports" / "600519.cn"
        reports_root.mkdir(parents=True)
        (reports_root / "2026-11-08.md").write_text("# r", encoding="utf-8")
        body = client.get("/api/reports").json()
        assert (
            body["reports"][0]["code"] == "600519"
            and body["reports"][0]["latest"] == "2026-11-08.md"
        )
