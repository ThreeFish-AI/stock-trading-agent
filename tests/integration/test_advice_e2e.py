"""T2 建议卡端到端：提交→会话→断言器→写者例外落卡→面板⑤读到；降级/锚③/闸/locks 回归。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from stock_trading_agent.cc_client.contract import CCResult
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.server.app import create_app


class CardCC(DryRunCCClient):
    """合成指定 structured_output 的通道（默认走 DryRun 全量卡载荷）。"""

    def __init__(self, structured: dict | None = None) -> None:
        super().__init__()
        self._structured = structured

    def spawn_once(self, req):
        if self._structured is None:
            return super().spawn_once(req)
        return CCResult(
            exit_code=0,
            envelope={"subtype": "success", "session_id": req.session_uuid, "total_cost_usd": 0.0},
            structured_output=dict(self._structured),
            result_line_present=True,
            transcript_path=Path("/dev/null"),
            usage_tokens={"input_tokens": 0, "output_tokens": 0},
        )


def make_app(tmp_path: Path, cc=None):
    from stock_trading_agent.loop.t1 import T1Runner
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout(root=tmp_path / "state")
    runtime = LoopRuntime(
        layout=layout,
        cc=cc if cc is not None else DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    return create_app(state_root=layout.root, runtime=runtime)


class TestFullChain:
    def test_card_written_and_served(self, tmp_path: Path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post(
                "/api/queue/submit", json={"kind": "monitor_review", "code": "600519"}
            )
            assert resp.status_code == 201
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "done"

            day = market_today()
            layout_root = Path(client.app.state.state_root)
            card_file = layout_root / "monitor" / "advice" / day / "600519.md"
            assert card_file.exists(), "建议卡应落盘（写者例外）"
            md = card_file.read_text(encoding="utf-8")
            assert "monitor-advice/1" in md and "持有观察" in md
            assert "不构成投资建议" in md  # 免责尾注

            body = client.get("/api/advice").json()
            assert body["cards"] and body["cards"][0]["code"] == "600519"
            assert body["cards"][0]["candidate_action"] == "持有观察"

            detail = client.get(f"/api/advice/{day}/600519").json()
            assert detail["md"] == md

            frag = client.get("/api/panel/advice").text
            assert "600519" in frag and "持有观察" in frag

            decisions = client.get("/api/decisions").json()
            kinds = [d["kind"] for d in decisions]
            assert "advice_card_written" in kinds and "task_done" in kinds

    def test_schema_violation_refused_no_card(self, tmp_path: Path):
        bad = CardCC(
            structured={  # 非法候选动作 → 断言拒 → 无卡
                "kind": "monitor_review",
                "outcome": "card",
                "disclaimer": "x",
                "candidate_action": "梭哈",
                "final_signal": "hold",
                "verdict_date": market_today(),
                "opinion_data_date": market_today(),
                "replayed_opinions": 2,
                "t1_sell_pct": 50,
                "t1_sell_tier": "半",
                "prev_diff": {"prev_final_signal": "none", "note": "n"},
                "trend_ref": {
                    "band_rows": [{"horizon": "1d", "low": 1, "high": 2, "prob_touch_low_pct": 30}]
                },
                "rationale": "r",
            }
        )
        app = make_app(tmp_path, cc=bad)
        with TestClient(app) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "failed"
            day = market_today()
            assert not (Path(client.app.state.state_root) / "monitor" / "advice" / day).exists()
            decisions = client.get("/api/decisions").json()
            assert any(d["kind"] == "advice_refused" for d in decisions)

    def test_facts_only_degrades_done_without_card(self, tmp_path: Path):
        degraded = CardCC(
            structured={
                "kind": "monitor_review",
                "outcome": "facts_only",
                "facts_note": "触发事实：跌破止损线，待收盘复核。",
                "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
            }
        )
        app = make_app(tmp_path, cc=degraded)
        with TestClient(app) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "done"  # 会话自报降级=正常 done
            day = market_today()
            advice_dir = Path(client.app.state.state_root) / "monitor" / "advice"
            assert not (advice_dir / day / "600519.md").exists()
            refused = client.get("/api/advice").json()["refused"]
            assert refused and refused[0]["origin"] == "session_degraded"
            assert "待收盘复核" in refused[0]["facts_note"]

    def test_anchor3_end_to_end(self, tmp_path: Path):
        layout_root = tmp_path / "state"
        layout_root.mkdir()
        (layout_root / "signals.json").write_text(
            json.dumps([{"code": "600519", "signal": "hold"}]), encoding="utf-8"
        )
        aggressive = CardCC(
            structured={
                "kind": "monitor_review",
                "outcome": "card",
                "disclaimer": "不构成投资建议；AI 生成，用户复核前置",
                "candidate_action": "可加仓",
                "final_signal": "buy",
                "verdict_date": market_today(),
                "opinion_data_date": market_today(),
                "replayed_opinions": 2,
                "t1_sell_pct": 100,
                "t1_sell_tier": "全部",
                "prev_diff": {"prev_final_signal": "hold", "note": "n"},
                "trend_ref": {
                    "band_rows": [{"horizon": "1d", "low": 1, "high": 2, "prob_touch_low_pct": 30}]
                },
                "rationale": "r",
            }
        )
        app = make_app(tmp_path, cc=aggressive)
        with TestClient(app) as client:
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "600519"})
            task = client.app.state.loop_runtime.consume_one()
            assert task is not None and task.status == "failed"
            assert "锚③" in (task.error or "")
            day = market_today()
            assert not (Path(client.app.state.state_root) / "monitor" / "advice" / day).exists()

    def test_quota_gate_end_to_end(self, tmp_path: Path):
        app = make_app(tmp_path)
        runtime = app.state.loop_runtime
        market_today()
        # 预置 5 条当日 done monitor_review（不同 code，enqueued_at=now 即市场今日）
        with TestClient(app) as client:
            for i in range(5):
                client.post(
                    "/api/queue/submit", json={"kind": "monitor_review", "code": f"60051{i}"}
                )
                runtime.consume_one()
            client.post("/api/queue/submit", json={"kind": "monitor_review", "code": "601919"})
            task = runtime.consume_one()
            assert task is not None and task.status == "failed"
            assert "5 票" in (task.error or "")
            # 无 universe（不可读）且超额 → 宁拒不漏；universe 在场且 code=real → 放行
            refused = client.get("/api/advice").json()["refused"]
            assert refused and refused[-1]["origin"] == "gate"


class TestLocksRegression:
    def test_status_locks_held_vs_stale(self, tmp_path: Path):
        from conftest import hold_lock

        app = make_app(tmp_path)
        layout_root = Path(app.state.state_root)
        (layout_root / "monitor").mkdir(parents=True, exist_ok=True)
        (layout_root / "monitor" / "watch.lock").touch()  # 陈旧锁文件
        with TestClient(app) as client:
            body = client.get("/api/monitor/status").json()
            assert body["locks"]["watch"] is False  # 存在≠持有（0.0.5 收敛回归钉）
            with hold_lock(layout_root / "monitor" / "watch.lock"):
                body = client.get("/api/monitor/status").json()
                assert body["locks"]["watch"] is True
