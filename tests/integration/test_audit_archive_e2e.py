"""0.0.21 audit/ 归档只读端到端：日期索引/转录 tail/路径三保险/sessions 互查链接/⑦卡。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.sessions import SessionRecord, SessionsState
from stock_trading_agent.state.store import atomic_write_json


def _make(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    rt = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
    app = create_app(state_root=layout.root, runtime=rt)
    day = layout.audit_dir / "2026-11-08"
    day.mkdir(parents=True, exist_ok=True)
    (day / "aabbccdd-0000-0000-0000-000000000001.jsonl").write_text(
        '{"type":"a"}\n{"type":"b"}\n{"type":"c"}\n', encoding="utf-8"
    )
    (day / "dryrun-diagnose_quick:600519:2026-11-08:100000.jsonl").write_text(
        '{"type":"dry"}\n', encoding="utf-8"
    )
    return app, rt, layout


class TestAuditRead:
    def test_dates_and_index(self, tmp_path: Path):
        app, _, _ = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert client.get("/api/audit/dates").json()["dates"] == ["2026-11-08"]
            body = client.get("/api/audit/2026-11-08").json()
            assert body["count"] == 2
            names = {f["file"] for f in body["files"]}
            assert "dryrun-diagnose_quick:600519:2026-11-08:100000.jsonl" in names

    def test_read_tail_clamped(self, tmp_path: Path):
        app, _, _ = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            body = client.get(
                "/api/audit/2026-11-08/aabbccdd-0000-0000-0000-000000000001.jsonl?tail=2"
            ).json()
            assert body["total_lines"] == 3 and body["lines"] == ['{"type":"b"}', '{"type":"c"}']

    def test_path_traversal_404(self, tmp_path: Path):
        app, _, _ = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert client.get("/api/audit/2026-11-08/..%2F..%2Fdecisions.jsonl").status_code == 404
            assert client.get("/api/audit/bad-date").status_code == 404
            assert client.get("/api/audit/2026-11-08/missing.jsonl").status_code == 404
            assert client.get("/api/audit/2026-11-09").status_code == 404

    def test_panel_fragments_and_sessions_link(self, tmp_path: Path):
        app, _, layout = _make(tmp_path)
        state = SessionsState()
        state.upsert(
            SessionRecord(
                session_uuid="aabbccdd-0000-0000-0000-000000000001",
                task_id="diagnose_quick:600519:2026-11-08:100000",
                transcript_path=str(
                    layout.audit_dir / "2026-11-08" / "aabbccdd-0000-0000-0000-000000000001.jsonl"
                ),
                usage_tokens={"total": 10},
                cost_usd_equiv=0.01,
                ts="2026-11-08T02:00:00+00:00",
            )
        )
        atomic_write_json(layout.sessions_path, state.to_dict())
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/audit-archive").text
            assert "2026-11-08" in frag and "查看" in frag
            assert 'id="au-transcript"' not in frag  # 0.0.24 容器外置（重拉不擦转录）
            detail = client.get(
                "/api/panel/audit-archive/2026-11-08/aabbccdd-0000-0000-0000-000000000001.jsonl"
            ).text
            assert (
                "{&quot;type&quot;:&quot;a&quot;}" in detail and "共 3 行" in detail
            )  # _esc 全转义（XSS 防线）
            sess = client.get("/api/panel/sessions").text
            assert 'hx-get="/api/panel/audit-archive/2026-11-08/' in sess  # 互查链接
        html = Path("src/stock_trading_agent/server/static/index.html").read_text(encoding="utf-8")
        assert 'id="au-archive"' in html and 'id="au-transcript"' in html  # 容器外置到壳层
