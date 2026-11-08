"""0.0.20 公司行动手动馈送端到端：split/dividend 入账、幂等 409、无持仓 422、
决策 payload 零敏感量、面板④表单与登记表（13-paper §④ M2 承诺闭合）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper.model import SimPortfolio, Tranche
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout


def _make(tmp_path: Path, *, with_position: bool = True):
    layout = StateLayout(root=tmp_path / "state")
    if with_position:
        pf = SimPortfolio(budget=100_000, cash=100_000)
        pf = paper_ledger.open_position(
            pf, "600519", "A", [Tranche(leg="T2_limit", lots=2, ref_price=10.0)], entry_ref=10.0
        )
        pos = pf.position("600519")
        pos.lots = 2
        pos.avg_cost = 10.0
        paper_ledger.persist(pf, layout.sim_portfolio_path)
    rt = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
    return create_app(state_root=layout.root, runtime=rt), rt, layout


def _decisions(layout: StateLayout) -> list[dict]:
    return [
        json.loads(r) for r in layout.decisions_path.read_text(encoding="utf-8").splitlines() if r
    ]


class TestFeed:
    def test_split_feed_end_to_end(self, tmp_path: Path):
        app, _, layout = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            resp = client.post(
                "/api/paper/corporate-action",
                json={
                    "kind": "split",
                    "code": "600519",
                    "ex_date": "2026-11-20",
                    "ratio_num": 15,
                    "ratio_den": 10,
                },
            )
            assert resp.status_code == 200, resp.text
        pf = paper_ledger.load(layout.sim_portfolio_path)
        assert pf.position("600519").lots == 3  # 2×1.5 向下取整
        assert len(pf.corporate_actions) == 1 and pf.corporate_actions[0].kind == "split"
        ca = [d for d in _decisions(layout) if d["kind"] == "paper_corporate_action"]
        assert ca and ca[-1]["payload"] == {
            "code": "600519",
            "action_kind": "split",
            "ex_date": "2026-11-20",
            "ratio_num": 15,
            "ratio_den": 10,
        }  # 零敏感量：每股额/税/找零禁入（NR-5）

    def test_dividend_feed_end_to_end(self, tmp_path: Path):
        app, _, layout = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            resp = client.post(
                "/api/paper/corporate-action",
                json={
                    "kind": "dividend",
                    "code": "600519",
                    "ex_date": "2026-11-20",
                    "pay_date": "2026-11-27",
                    "per_share_cash": 0.5,
                },
            )
            assert resp.status_code == 200, resp.text
        pf = paper_ledger.load(layout.sim_portfolio_path)
        # 2 手 × 100 股 × 0.5 元 = 100 元税前；FIFO 基不齐余量按 20% 保守 → 净 80
        assert pf.cash == 100_080.0
        assert pf.corporate_actions[0].tax == 20.0

    def test_duplicate_same_basis_409(self, tmp_path: Path):
        app, _, _ = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            body = {
                "kind": "split",
                "code": "600519",
                "ex_date": "2026-11-20",
                "ratio_num": 15,
                "ratio_den": 10,
            }
            assert client.post("/api/paper/corporate-action", json=body).status_code == 200
            assert client.post("/api/paper/corporate-action", json=body).status_code == 409

    def test_no_position_422_and_bad_params_422(self, tmp_path: Path):
        app_nopos, _, _ = _make(tmp_path, with_position=False)
        with TestClient(app_nopos, headers=ORIGIN_HEADERS) as client:
            resp = client.post(
                "/api/paper/corporate-action",
                json={
                    "kind": "split",
                    "code": "600519",
                    "ex_date": "2026-11-20",
                    "ratio_num": 15,
                    "ratio_den": 10,
                },
            )
            assert resp.status_code == 422
        app, _, _ = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert (
                client.post(  # split 缺比率
                    "/api/paper/corporate-action",
                    json={"kind": "split", "code": "600519", "ex_date": "2026-11-20"},
                ).status_code
                == 422
            )
            assert (
                client.post(  # dividend 缺 pay_date
                    "/api/paper/corporate-action",
                    json={
                        "kind": "dividend",
                        "code": "600519",
                        "ex_date": "2026-11-20",
                        "per_share_cash": 0.5,
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(  # 非法 code
                    "/api/paper/corporate-action",
                    json={
                        "kind": "dividend",
                        "code": "abc",
                        "ex_date": "2026-11-20",
                        "pay_date": "2026-11-27",
                        "per_share_cash": 0.5,
                    },
                ).status_code
                == 422
            )

    def test_panel_form_variant_and_registry_table(self, tmp_path: Path):
        app, _, _layout = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/paper").text
            assert "/api/panel/paper/corporate-action" in frag and "馈送入账" in frag
            assert "公司行动登记" in frag
            resp = client.post(
                "/api/panel/paper/corporate-action",
                data={
                    "kind": "dividend",
                    "code": "600519",
                    "ex_date": "2026-11-20",
                    "pay_date": "2026-11-27",
                    "per_share_cash": "0.5",
                },
            )
            assert resp.status_code == 200
            assert "公司行动已入账" in resp.text
            frag = client.get("/api/panel/paper").text
            assert "分红" in frag and "0.5 元/股" in frag  # 登记表口径列
