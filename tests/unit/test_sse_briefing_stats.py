"""PR-E 纯函数族：事件名映射、stats 聚合、briefing 卡脱敏负断言、DirWatcher mtime diff。"""

from __future__ import annotations

from pathlib import Path

from stock_trading_agent.loop.briefing import (
    build_card_md,
    has_ack,
    persist_card,
    scan_briefing_text,
)
from stock_trading_agent.loop.dirwatch import DirWatcher
from stock_trading_agent.server.stats import aggregate_decisions, budget_curve
from stock_trading_agent.server.stream_hub import StreamHub, to_event_name
from stock_trading_agent.state.paths import StateLayout


def _layout(tmp_path: Path) -> StateLayout:
    return StateLayout(root=tmp_path / "state")


class TestEventNames:
    def test_mapping_table(self, tmp_path: Path):
        layout = _layout(tmp_path)
        cases = {
            layout.root / "agent" / "queue.json": "queue",
            layout.root / "agent" / "decisions.jsonl": "decisions",
            layout.root / "agent" / "budget.json": "budget",
            layout.root / "agent" / "briefing" / "2026-11-08" / "600519.md": "briefing",
            layout.root / "monitor" / "events" / "2026-11-08.jsonl": "monitor",
            layout.root / "monitor" / "advice" / "2026-11-08" / "600519.md": "advice",
            layout.root / "monitor" / "state.json": "status",
            layout.root / "outlook" / "2026-11-08.md": "outlook",
            layout.root / "sim-portfolio.json": "paper",
            layout.root / "sim-pending-fills.json": "paper",
            Path("/elsewhere/x"): None,
        }
        for path, expected in cases.items():
            assert to_event_name(path, layout) == expected, path

    def test_outside_root_none(self, tmp_path: Path):
        assert to_event_name(Path("/tmp/other.json"), _layout(tmp_path)) is None


class TestDirWatcher:
    def test_mtime_diff_incremental(self, tmp_path: Path):
        layout = _layout(tmp_path)
        events_dir = layout.monitor_events_dir
        events_dir.mkdir(parents=True)
        watcher = DirWatcher(
            roots=(events_dir,), on_change=lambda e: None, to_event=lambda p: "monitor"
        )
        assert watcher.poll_once() == set()  # 基线扫描
        f = events_dir / "2026-11-08.jsonl"
        f.write_text("{}\n", encoding="utf-8")
        assert watcher.poll_once() == {"monitor"}
        assert watcher.poll_once() == set()  # 无新变更
        f.write_text("{}\n{}\n", encoding="utf-8")  # mtime 变
        assert watcher.poll_once() == {"monitor"}


class TestStreamHub:
    def test_publish_without_loop_silent(self):
        hub = StreamHub()
        hub.publish_threadsafe({"queue"})  # 无 loop/订阅：静默不抛

    def test_publish_fanout(self):
        import asyncio

        hub = StreamHub()

        async def scenario():
            hub.bind_loop(asyncio.get_running_loop())
            q = hub.subscribe()
            hub.publish_threadsafe({"queue", "decisions"})
            got = await asyncio.wait_for(q.get(), timeout=1)
            assert got == frozenset({"queue", "decisions"})
            hub.unsubscribe(q)

        asyncio.run(scenario())


class TestStats:
    def test_aggregate_and_insufficient_note(self):
        lines = [
            {"kind": "task_submitted"},
            {"kind": "task_done"},
            {"kind": "task_done"},
            {"kind": "task_failed"},
            {"kind": "advice_card_written"},
            {"kind": "advice_card_written"},
        ]
        agg = aggregate_decisions(lines)
        assert agg["done"] == 2 and agg["failed"] == 1 and agg["total"] == 3
        assert agg["success_rate"] == round(2 / 3, 4)
        assert agg["sufficient_sample"] is False and "样本 3" in agg["note"]
        assert agg["kinds"]["advice_card_written"] == 2

    def test_aggregate_empty(self):
        agg = aggregate_decisions([])
        assert agg["success_rate"] is None and agg["total"] == 0

    def test_budget_curve_daily_buckets(self):
        records = [
            {
                "ts": "2026-11-07T17:30:00+00:00",
                "usage_tokens": {"input_tokens": 10},
                "cost_usd_equiv": 0.1,
            },  # 上海 11-08 01:30
            {
                "ts": "2026-11-08T03:00:00+00:00",
                "usage_tokens": {"output_tokens": 5},
                "cost_usd_equiv": 0.2,
            },
        ]
        curve = budget_curve(records)
        assert [c["date"] for c in curve] == ["2026-11-08"]
        assert curve[0]["tokens"] == 15 and curve[0]["sessions"] == 2


class TestBriefing:
    def test_build_and_scan_clean(self):
        md = build_card_md(
            {"briefing": "收盘价跌破 MA20，待观察。", "action_items": ["止损线距离"]},
            date="2026-11-08",
            code="600519",
        )
        assert scan_briefing_text(md) == []
        assert "复核：止损线距离" in md
        assert "不构成投资建议" in md

    def test_quantity_negative_assertion(self):
        md = build_card_md({"briefing": "持仓 1200 股待观察。"}, date="2026-11-08", code="600519")
        assert any("数量词" in r for r in scan_briefing_text(md))

    def test_directive_word_refused(self):
        md = build_card_md({"action_items": ["x"]}, date="2026-11-08", code="600519").replace(
            "复核：x", "复核：买入"
        )
        assert any("指令词" in r for r in scan_briefing_text(md))

    def test_persist_and_ack(self, tmp_path: Path):
        layout = _layout(tmp_path)
        md = build_card_md({"briefing": "b"}, date="2026-11-08", code="600519")
        path = persist_card(layout, "2026-11-08", "600519", md)
        assert path.exists() and "600519" in path.read_text(encoding="utf-8")
        assert not has_ack([], date="2026-11-08", code="600519")
        acked = [{"kind": "briefing_ack", "payload": {"date": "2026-11-08", "code": "600519"}}]
        assert has_ack(acked, date="2026-11-08", code="600519")
        assert not has_ack(acked, date="2026-11-09", code="600519")
