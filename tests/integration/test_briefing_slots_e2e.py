"""0.0.18 早晚分卡端到端：全链落 .am/.pm 卡 + 决策 session + 面板分组 + ack 槽位
+ worker 自动触发（L2 闸、universe、幂等、universe 不可读留痕）。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from conftest import ORIGIN_HEADERS
from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.t1 import T1Runner
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.server.app import create_app
from stock_trading_agent.state.agent_config import load_agent_config, save_agent_config
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.store import atomic_write_json

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _make(tmp_path: Path, *, l2: bool = False, universe: list[dict] | None = None):
    layout = StateLayout(root=tmp_path / "state")
    if universe is not None:
        layout.monitor_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(layout.universe_path, {"rows": universe})
    rt = LoopRuntime(
        layout=layout,
        cc=DryRunCCClient(audit_dir=layout.audit_dir),
        t1=T1Runner(),
        watcher_enabled=False,
    )
    if l2:
        cfg = load_agent_config(layout.agent_config_path)
        cfg.l2_enabled = True
        save_agent_config(layout.agent_config_path, cfg)
    return create_app(state_root=layout.root, runtime=rt), rt, layout


def _sh(hour: int, minute: int, day: int = 9) -> dt.datetime:  # 2026-11-09 周一
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=SHANGHAI)


class TestFullChain:
    def test_morning_card_end_to_end(self, tmp_path: Path):
        app, rt, layout = _make(tmp_path)
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            resp = client.post(
                "/api/queue/submit", json={"kind": "briefing_morning", "code": "600519"}
            )
            assert resp.status_code == 201
        task = rt.consume_one()
        assert task is not None and task.status == "done"
        # 卡名按实际交易日断言（跨日消费防脆断——扫目录取事实卡名）
        cards = sorted(p.name for p in layout.briefing_dir.rglob("600519.*.md"))
        assert cards == ["600519.am.md"], cards
        md = next(p for p in layout.briefing_dir.rglob("600519.am.md")).read_text(encoding="utf-8")
        assert "session: am" in md and "盘前晨报" in md
        decisions = [
            json.loads(r)
            for r in layout.decisions_path.read_text(encoding="utf-8").splitlines()
            if r
        ]
        card_lines = [d for d in decisions if d["kind"] == "briefing_card"]
        assert card_lines and card_lines[-1]["payload"].get("session") == "am"

    def test_panel_groups_and_ack_roundtrip(self, tmp_path: Path):
        from stock_trading_agent.loop.briefing import persist_card
        from stock_trading_agent.loop.monitor_events import market_today

        app, rt, layout = _make(tmp_path)
        day = market_today()
        persist_card(
            layout, day, "600519", "---\ncode: '600519'\nsession: am\n---\nam 卡\n", slot="am"
        )
        persist_card(layout, day, "600519", "---\ncode: '600519'\n---\nlegacy 卡\n")
        assert rt.start()
        with TestClient(app, headers=ORIGIN_HEADERS) as client:
            frag = client.get("/api/panel/briefing").text
            assert "盘前晨报</h4>" in frag and "通用（legacy briefing_review）</h4>" in frag
            resp = client.post(
                "/api/panel/briefing/ack", data={"date": day, "code": "600519", "session": "am"}
            )
            assert resp.status_code == 200
            frag = client.get("/api/panel/briefing").text
            am_block = frag.split("盘前晨报</h4>")[1].split("通用（legacy")[0]
            assert "已确认" in am_block  # am ack 只确认 am 卡
            legacy_block = frag.split("通用（legacy briefing_review）</h4>")[1]
            assert "待确认" in legacy_block
        rt.stop()


class TestAutoBriefing:
    def test_morning_auto_enqueue_idempotent(self, tmp_path: Path):
        _app, rt, _layout = _make(
            tmp_path,
            l2=True,
            universe=[{"code": "600519", "kind": "real"}, {"code": "000001", "kind": "sim"}],
        )
        planned = rt._maybe_auto_briefing(now=_sh(9, 10))
        assert {(t.kind, t.code) for t in planned} == {
            ("briefing_morning", "600519"),
            ("briefing_morning", "000001"),
        }
        again = rt._maybe_auto_briefing(now=_sh(9, 12))
        assert again == []  # 每股每槽每日一次（含终态 dedup）
        evening = rt._maybe_auto_briefing(now=_sh(18, 20))
        assert {(t.kind, t.code) for t in evening} == {
            ("briefing_evening", "600519"),
            ("briefing_evening", "000001"),
        }
        queue = rt.loop.load_queue()
        assert len([t for t in queue.tasks if t.source == "monitor"]) == 4

    def test_l2_off_no_enqueue(self, tmp_path: Path):
        _, rt, _ = _make(tmp_path, l2=False, universe=[{"code": "600519", "kind": "real"}])
        assert rt._maybe_auto_briefing(now=_sh(9, 10)) == []

    def test_universe_unreadable_logged(self, tmp_path: Path):
        _app, rt, layout = _make(tmp_path, l2=True, universe=None)  # 不落 universe.json
        assert rt._maybe_auto_briefing(now=_sh(9, 10)) == []
        decisions = [
            json.loads(r)
            for r in layout.decisions_path.read_text(encoding="utf-8").splitlines()
            if r
        ]
        assert any(d["kind"] == "briefing_auto_skipped" for d in decisions)

    def test_outside_window_noop(self, tmp_path: Path):
        _, rt, _ = _make(tmp_path, l2=True, universe=[{"code": "600519", "kind": "real"}])
        assert rt._maybe_auto_briefing(now=_sh(12, 0)) == []
