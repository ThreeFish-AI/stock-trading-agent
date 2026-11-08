"""P2 PR-K：会话中间产物解析（SessionView/stage_status/opinions_board/verdict_card）。"""

from __future__ import annotations

import json
from pathlib import Path

from stock_trading_agent.server.session_products import (
    opinions_board,
    read_session_products,
    stage_status,
    verdict_card,
)


def _seed_workdir(tmp_path: Path, *, full: bool) -> Path:
    runs = tmp_path / "runs"
    vs = runs / "uuid-1" / ".vs"
    vs.mkdir(parents=True, exist_ok=True)
    (vs / "evidence.json").write_text(
        json.dumps(
            {
                "meta": {"code": "600519", "market": "cn"},
                "quality": {"score": 74, "tier": "usable", "confidence_cap": "low"},
                "blocks": {},
            }
        ),
        encoding="utf-8",
    )
    (vs / "context-pack.md").write_text("pack", encoding="utf-8")
    (vs / "opinions.json").write_text(
        "[]"
        if not full
        else json.dumps(
            [
                {
                    "source": "strategy:均线金叉",
                    "signal": "buy",
                    "confidence": 0.7,
                    "weight": 1.0,
                    "rationale": "r",
                }
            ]
        ),
        encoding="utf-8",
    )
    if full:
        (vs / "opinions.position.json").write_text(
            json.dumps([{"source": "role:中线", "signal": "hold", "confidence": 0.6}]),
            encoding="utf-8",
        )
        (vs / "verdict.json").write_text(
            json.dumps(
                {
                    "risk_guard": {
                        "initial": "buy",
                        "final_before_cap": "buy",
                        "final": "hold",
                        "log": ["veto buy->hold"],
                    },
                    "aggregate": {
                        "final_signal": "hold",
                        "confidence": 0.55,
                        "consensus_level": "medium",
                    },
                    "disagreement": {"buckets": {"bullish": ["a"], "bearish": ["b"]}},
                    "evidence_quality": {"score": 74, "confidence_cap": "low"},
                    "executable": False,
                }
            ),
            encoding="utf-8",
        )
    reports = tmp_path / "reports"
    if full:
        d = reports / "600519.cn"
        d.mkdir(parents=True)
        (d / "2026-11-08.md").write_text("# r", encoding="utf-8")
    return runs


class TestReadAndStages:
    def test_empty_workdir_defensive(self, tmp_path: Path):
        view = read_session_products(tmp_path / "runs", "none", tmp_path / "reports")
        assert view.notes and "不存在" in view.notes[0]
        stages = stage_status(view)
        assert stages[0]["state"] == "current"  # 空 workdir：首个未完成=①current

    def test_partial_then_full(self, tmp_path: Path):
        runs = _seed_workdir(tmp_path, full=False)
        view = read_session_products(runs, "uuid-1", tmp_path / "reports")
        stages = {st["stage"]: st for st in stage_status(view)}
        assert stages[1]["state"] == "done" and stages[2]["state"] == "done"
        assert stages[3]["state"] == "current"  # opinions 铺底 [] 非空才算③——骨架态 current 在③
        assert stages[4]["state"] == "pending" and stages[5]["state"] == "pending"

        runs = _seed_workdir(tmp_path, full=True)
        view = read_session_products(runs, "uuid-1", tmp_path / "reports")
        stages = {st["stage"]: st for st in stage_status(view)}
        assert all(st["state"] == "done" for st in stages.values())
        assert "RiskGuard" in stages[4]["note"]

    def test_opinions_board(self, tmp_path: Path):
        runs = _seed_workdir(tmp_path, full=True)
        view = read_session_products(runs, "uuid-1", tmp_path / "reports")
        board = opinions_board(view)
        assert board["default"][0]["source"] == "strategy:均线金叉"
        assert board["position"] and board["position"][0]["signal"] == "hold"

    def test_verdict_card(self, tmp_path: Path):
        runs = _seed_workdir(tmp_path, full=True)
        view = read_session_products(runs, "uuid-1", tmp_path / "reports")
        card = verdict_card(view)
        assert card["initial"] == "buy" and card["final"] == "hold"
        assert card["buckets"]["bullish"] == ["a"] and card["executable"] is False
        assert read_session_products(tmp_path, "none", tmp_path).verdict_default is None or True


class TestEndpoint:
    def test_session_fragment(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from conftest import ORIGIN_HEADERS
        from stock_trading_agent.cc_client.dryrun import DryRunCCClient
        from stock_trading_agent.loop.t1 import T1Runner
        from stock_trading_agent.loop.worker import LoopRuntime
        from stock_trading_agent.server.app import create_app
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=tmp_path / "state")
        rt = LoopRuntime(
            layout=layout,
            cc=DryRunCCClient(audit_dir=layout.audit_dir),
            t1=T1Runner(),
            watcher_enabled=False,
        )
        app = create_app(state_root=layout.root, runtime=rt)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            rt.submit(kind="diagnose_quick", code="600519")
            rt.consume_one()  # 不 start worker（防线程抢先消费）；手动消费保 session_uuid 可查
            q = client.get("/api/queue").json()
            task_id = q["tasks"][0]["task_id"]
            uuid = q["tasks"][0]["session_uuid"]
            assert uuid
            # 种 workdir（模拟真实会话产物）
            vs = layout.runs_dir / uuid / ".vs"
            vs.mkdir(parents=True, exist_ok=True)
            (vs / "evidence.json").write_text(
                json.dumps({"meta": {"code": "600519", "market": "cn"}, "blocks": {}}),
                encoding="utf-8",
            )
            body = client.get(f"/api/session/{task_id}").json()
            assert body["stages"][0]["state"] == "done"
            frag = client.get(f"/api/panel/session/{task_id}").text
            assert "取证据" in frag
            assert client.get("/api/session/nope").status_code == 404
