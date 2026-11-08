"""决策链完整性端到端（0.0.15）：写端点链有效、断链降级只读、repair-decisions 修复闭环。

生产事故背景：serve 降级只读态下写端点以空内存链从 seq=1 落盘（W1）+ 游标漂移双写（W2）
+ 重载不重置游标重放历史（W3）——57 行仅 11 个唯一 seq。本文件钉死修复后行为。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.paths import StateLayout


def _make_app(tmp_path: Path):
    layout = StateLayout(root=tmp_path / "state")
    rt = LoopRuntime(layout=layout, t1=T1Runner(), watcher_enabled=False)
    return create_app(state_root=layout.root, runtime=rt), rt, layout


def _chain_ok(layout: StateLayout) -> bool:
    return DecisionLog.load(layout.decisions_path).verify_chain()


def _break_chain(layout: StateLayout) -> None:
    """注入 W1 形态断链：追加一段从 seq=1 重新起的孤立行。"""
    orphan = DecisionLog()
    orphan.append("2026-11-08T00:00:00+00:00", "task_done", {"task_id": "z"})
    with layout.decisions_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(orphan.lines[0]), ensure_ascii=False) + "\n")


def test_paper_confirm_keeps_chain_valid(tmp_path: Path):
    from stock_trading_agent.loop.monitor_events import parse_line
    from stock_trading_agent.paper import ledger as paper_ledger
    from stock_trading_agent.paper.model import SimPortfolio, Tranche

    app, rt, layout = _make_app(tmp_path)
    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = paper_ledger.open_position(
        pf, "600519", "A", [Tranche(leg="T2_limit", lots=1, ref_price=10.0)], entry_ref=10.0
    )
    pos = pf.position("600519")
    pos.lots = 1
    pos.avg_cost = 10.0
    paper_ledger.persist(pf, layout.sim_portfolio_path)
    ev = parse_line(
        json.dumps(
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
    )
    rt.record_monitor_event(ev)
    with TestClient(app, headers=ORIGIN_HEADERS) as client:
        pending_view = client.get("/api/paper/view").json()
        request_id = pending_view["pending"][0]["request_id"]
        assert client.post("/api/paper/confirm", json={"request_id": request_id}).status_code == 200
        assert _chain_ok(layout)  # 确认链全程链有效（W1/W2 防回退）


def test_l2_toggle_and_briefing_ack_keep_chain_valid(tmp_path: Path):
    app, rt, layout = _make_app(tmp_path)
    with TestClient(app, headers=ORIGIN_HEADERS) as client:
        assert rt.start()
        assert client.post("/api/l2", json={"enabled": True}).status_code == 200
        # briefing 卡先落盘再 ack（当日卡才可确认：直接经 runtime 链路落卡）
        from stock_trading_agent.loop.briefing import persist_card

        card = layout.briefing_card_path("2026-11-08", "600519")
        persist_card(layout, "2026-11-08", "600519", "# 盘前晨报 600519\n复核项：示例\n")
        assert card.exists()
        resp = client.post("/api/panel/briefing/ack", data={"date": "2026-11-08", "code": "600519"})
        assert resp.status_code == 200
        lines = [
            json.loads(r)
            for r in layout.decisions_path.read_text(encoding="utf-8").splitlines()
            if r
        ]
        kinds = [x["kind"] for x in lines]
        assert "l2_toggled" in kinds and "briefing_ack" in kinds
        assert [x["seq"] for x in lines] == list(range(1, len(lines) + 1))
        assert _chain_ok(layout)
        rt.stop()


def test_broken_chain_degrades_readonly_with_503_hint(tmp_path: Path):
    """断链文件：serve 降级只读（worker 不启动）、写端点 503 带修复指引、状态可见。"""
    app, rt, layout = _make_app(tmp_path)
    rt.submit(kind="diagnose_quick", code="600519")  # 先有完好链
    _break_chain(layout)
    with TestClient(app, headers=ORIGIN_HEADERS) as client:
        assert not rt.start()  # strict load 断链 → 不启动 worker（降级只读）
        assert rt.chain_error and "repair-decisions" in rt.chain_error
        resp = client.post("/api/queue/submit", json={"kind": "diagnose_quick", "code": "000001"})
        assert resp.status_code == 503
        assert "repair-decisions" in resp.json()["detail"]
        status = client.get("/api/monitor/status").json()
        assert status["loop"]["chain_error"]
        frag = client.get("/api/panel/syscard").text
        assert "决策链" in frag and "待修复" in frag  # syscard 断链行呈现


def test_assert_refuse_carries_reason_codes(tmp_path: Path):
    """0.0.22：断言拒绝决策行带 typed reason_codes（§15 分桶数据源）。"""
    from stock_trading_agent.cc_client.contract import CCRequest
    from stock_trading_agent.state.queue import QueueState, TaskRecord

    _app, rt, layout = _make_app(tmp_path)
    queue = QueueState()
    task = TaskRecord(
        task_id="diagnose_quick:600519:2026-11-08:100000",
        dedup_key="diagnose_quick:600519:2026-11-08",
        kind="diagnose_quick",
        code="600519",
        enqueued_at="2026-11-08T02:00:00+00:00",
    )
    queue.submit(task)
    queue.transition(task.task_id, "running")  # assert_and_persist 守卫要求 running 态
    from stock_trading_agent.cc_client.contract import CCResult

    bad = CCResult(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output=None,  # → structured_missing
        result_line_present=True,
        transcript_path="/tmp/x.jsonl",
        usage_tokens={},
    )

    rt.loop.decisions = DecisionLog()
    rt.loop.assert_and_persist(
        task,
        CCRequest(
            task_id=task.task_id,
            session_uuid="u" * 8 + "-0000-0000-0000-000000000001",
            prompt="/vibe-stock 600519",
            schema=None,
            budget_usd=1.0,
            max_turns=5,
            allowed_tools=("Read",),
        ),
        bad,
        queue=queue,
        sessions=rt.loop.load_sessions(),
    )
    rows = [
        json.loads(r) for r in layout.decisions_path.read_text(encoding="utf-8").splitlines() if r
    ]
    refused = [d for d in rows if d["kind"] == "assert_refuse"]
    assert refused and "structured_missing" in refused[0]["payload"]["reason_codes"]


def test_repair_cli_end_to_end(tmp_path: Path, monkeypatch):
    """CLI repair-decisions 黑盒：断链 → 修复 → strict load 通过 → noop 复跑。"""
    import stock_trading_agent.__main__ as cli

    monkeypatch.setenv("STA_STATE_ROOT", str(tmp_path / "state"))
    layout = StateLayout.from_env()
    log = DecisionLog()
    log.append("t1", "task_submitted", {"task_id": "a"}, path=layout.decisions_path)
    log.append("t2", "task_done", {"task_id": "a"}, path=layout.decisions_path)
    _break_chain(layout)
    from stock_trading_agent.state.decisions import DecisionChainError

    try:
        DecisionLog.load(layout.decisions_path)
    except DecisionChainError as exc:
        assert "repair-decisions" in str(exc)
    else:
        raise AssertionError("断链应被 strict load 拒绝")
    assert cli.main(["repair-decisions"]) == 0
    assert DecisionLog.load(layout.decisions_path).verify_chain()  # 修复后可载入
    rows = [
        json.loads(r) for r in layout.decisions_path.read_text(encoding="utf-8").splitlines() if r
    ]
    kinds = [r["kind"] for r in rows]
    assert kinds[-2:] == ["chain_repair_started", "chain_repair_done"]
    assert rows[0]["payload"] == {"task_id": "a"}  # 保行：原行内容不丢
    backups = list(layout.agent_dir.glob("decisions.jsonl.bak-*"))
    assert len(backups) == 1 and backups[0].exists()  # 备份不删行
    assert cli.main(["repair-decisions"]) == 0  # 完好链 noop
