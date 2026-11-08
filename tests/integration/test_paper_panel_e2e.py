"""模拟盘面板④端到端：t2 事件入队 → 确认落账 → fragment 呈现；驳回零迁移。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.monitor_events import parse_line
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.paper import ledger as paper_ledger
from stock_trading_agent.paper import pending as paper_pending
from stock_trading_agent.paper.model import SimPortfolio, Tranche
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.paths import StateLayout


def make_app(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    runtime = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
    return create_app(state_root=layout.root, runtime=runtime), layout


def seed_position(layout: StateLayout) -> None:
    """植入持仓：1 手 holding + T2 批 filled（建仓历史），供确认/卖出链。"""
    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = paper_ledger.open_position(
        pf, "600519", "A", [Tranche(leg="T2_limit", lots=1, ref_price=10.0)], entry_ref=10.0
    )
    pos = pf.position("600519")
    pos.lots = 1
    pos.avg_cost = 10.0
    paper_ledger.persist(pf, layout.sim_portfolio_path)


T2_EVENT = json.dumps(
    {
        "ts": "2026-11-07T02:00:00Z",
        "code": "600519",
        "market": "cn",
        "kind": "sim",
        "rule": "t2_pending_fill",
        "level": "T0",
        "notify": True,
        "review": "T2成交复核",
        "facts": {
            "leg": "T2_limit",
            "ref_price": 10.0,
            "lots": 1,
            "price": 9.98,
            "date": "2026-11-06",
        },
    },
    ensure_ascii=False,
)


class TestConfirmChain:
    def test_t2_event_to_confirm_to_ledger(self, tmp_path: Path):
        app, layout = make_app(tmp_path)
        seed_position(layout)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            runtime = client.app.state.loop_runtime
            ev = parse_line(T2_EVENT)
            assert ev is not None
            runtime.record_monitor_event(ev)
            queue = paper_pending.load(layout.sim_pending_fills_path)
            assert len(queue.requests) == 1
            request_id = queue.requests[0].request_id
            decisions = client.get("/api/decisions").json()
            assert any(d["kind"] == "paper_pending_fill" for d in decisions)

            resp = client.post("/api/paper/confirm", json={"request_id": request_id})
            assert resp.status_code == 200, resp.text

            pf = paper_ledger.load(layout.sim_portfolio_path)
            assert pf.position("600519").tranches[0].status == "filled"
            assert pf.position("600519").lots == 2  # 1 → 2
            assert pf.operations[-1].trade_date == "2026-11-06"
            queue_after = paper_pending.load(layout.sim_pending_fills_path)
            assert queue_after.find(request_id) is None  # 终态移除
            decisions = client.get("/api/decisions").json()
            assert any(d["kind"] == "paper_fill_confirmed" for d in decisions)

    def test_reject_keeps_tranche_pending(self, tmp_path: Path):
        app, layout = make_app(tmp_path)
        seed_position(layout)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            runtime = client.app.state.loop_runtime
            ev = parse_line(T2_EVENT)
            runtime.record_monitor_event(ev)
            queue = paper_pending.load(layout.sim_pending_fills_path)
            request_id = queue.requests[0].request_id

            resp = client.post("/api/paper/reject", json={"request_id": request_id})
            assert resp.status_code == 200
            pf = paper_ledger.load(layout.sim_portfolio_path)
            assert pf.position("600519").tranches[0].status == "pending"  # 批保持 pending 零迁移
            assert pf.position("600519").lots == 1
            assert paper_pending.load(layout.sim_pending_fills_path).find(request_id) is None

    def test_view_and_fragment(self, tmp_path: Path):
        app, layout = make_app(tmp_path)
        seed_position(layout)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            view = client.get("/api/paper/view").json()
            assert view["exists"] is True
            pos = view["positions"][0]
            assert pos["code"] == "600519" and "sellable_lots" in pos
            frag = client.get("/api/panel/paper").text
            assert "600519" in frag and "NR-2" in frag and "假设性" in frag
            # 0.0.15：绩效三格 + 盈亏红涨绿跌 + 中文列头 + colgroup
            assert "Sharpe" in frag and "最大回撤" in frag and "胜率" in frag
            assert 'class="pf' in frag or 'class="nf' in frag or "—" in frag
            assert '<th scope="col">建仓批</th>' in frag
            assert "<colgroup>" in frag

    def test_confirm_unknown_404_and_bad_request_422(self, tmp_path: Path):
        app, _ = make_app(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            assert client.post("/api/paper/confirm", json={"request_id": "nope"}).status_code == 404
            assert client.post("/api/paper/confirm", json={}).status_code == 422

    def test_sim_lock_serializes_concurrent_confirms(self, tmp_path: Path):
        """并发确认同 code 两笔卖出：第二笔的 T+1 冻结面包含第一笔（sim.lock 串行）。"""
        import threading

        from stock_trading_agent.paper.confirm import PendingFillRequest
        from stock_trading_agent.paper.fills import Bar

        app, layout = make_app(tmp_path)
        # 植入 2 手持仓（前日买入，全可卖）+ 两笔 pending 卖出各 2 手
        pf = SimPortfolio(budget=100_000, cash=100_000)
        pf = paper_ledger.open_position(pf, "600519", "A", [], entry_ref=10.0)
        pos = pf.position("600519")
        pos.lots = 2
        pos.avg_cost = 10.0
        paper_ledger.persist(pf, layout.sim_portfolio_path)
        q = paper_pending.PendingQueue()
        for rid in ("s1", "s2"):
            q.requests.append(
                PendingFillRequest(
                    request_id=rid,
                    code="600519",
                    tranche_leg="stop",
                    side="sell",
                    lots=2,
                    ref_price=10.0,
                    bar=Bar(date="2026-11-08", open=10, high=10, low=10, close=10),
                    ts="2026-11-08T02:00:00Z",
                )
            )
        paper_pending.save(q, layout.sim_pending_fills_path)

        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            results: dict[str, int] = {}

            def hit(rid: str) -> None:
                resp = client.post("/api/paper/confirm", json={"request_id": rid})
                results[rid] = resp.status_code

            t1 = threading.Thread(target=hit, args=("s1",))
            t2 = threading.Thread(target=hit, args=("s2",))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            assert sorted(results.values()) == [200, 409]  # 恰一笔落账、另一笔可卖不足被拒
            pf_after = paper_ledger.load(layout.sim_portfolio_path)
            assert pf_after.position("600519").lots == 0
