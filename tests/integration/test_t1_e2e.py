"""T1 接线端到端：monitor 台账 → watcher 增量 → 入队 → 锁内 defer → 补跑出新 pack。"""

from __future__ import annotations

import json
from pathlib import Path

from stock_trading_agent.loop.monitor_events import market_today
from stock_trading_agent.loop.t1 import T1Outcome


def _line(code: str, review: str | None = "止损复核", suppress: str | None = None) -> str:
    row = {
        "ts": "09:31:00",
        "code": code,
        "market": "cn",
        "kind": "real",
        "rule": "stop_break",
        "level": "T0",
        "notify": True,
        "review": review,
    }
    if suppress:
        row["suppress_reason"] = suppress
    return json.dumps(row, ensure_ascii=False)


class TestT1Chain:
    def test_seed_events_enqueue_t1_per_code(self, make_runtime):
        rt = make_runtime()
        f = rt.layout.monitor_events_file(market_today())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            _line("600519")
            + "\n"
            + _line("601919")
            + "\n"
            + _line("600519", suppress="cooldown")
            + "\n",
            encoding="utf-8",
        )
        from stock_trading_agent.loop.watcher import EventsWatcher

        watcher = EventsWatcher(layout=rt.layout, on_events=rt._on_monitor_events)
        assert len(watcher.poll_once()) == 3
        q = json.loads(rt.layout.queue_path.read_text(encoding="utf-8"))
        t1_tasks = [t for t in q["tasks"] if t["kind"] == "t1_refresh"]
        assert len(t1_tasks) == 2  # 600519（首个未压制行）+ 601919；压制行不入队
        decisions = [
            json.loads(x)["kind"]
            for x in rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        ]
        assert decisions.count("monitor_event") == 3  # 压制行也登记（台账 only）
        assert watcher.poll_once() == []  # 偏移断点：无新增

    def test_consume_t1_produces_pack_in_agent_runs(self, make_runtime):
        class FakeT1:
            def run(self, code: str, *, workdir: Path) -> T1Outcome:
                workdir.mkdir(parents=True, exist_ok=True)
                (workdir / "context-pack.md").write_text("# pack", encoding="utf-8")
                return T1Outcome(ok=True, workdir=workdir, reasons=())

        rt = make_runtime(t1=FakeT1())
        ev = json.loads(_line("600519"))
        from stock_trading_agent.loop.monitor_events import parse_line

        rt.record_monitor_event(parse_line(json.dumps(ev, ensure_ascii=False)))
        task = rt.consume_one()
        assert task is not None and task.status == "done"
        workdir = rt.layout.t1_workdir(market_today(), "600519")
        assert (workdir / "context-pack.md").exists()
        assert str(workdir).startswith(str(rt.layout.runs_dir))  # agent/ 命名空间内
        lines = rt.layout.decisions_path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["kind"] == "task_done"
        assert json.loads(lines[-1])["payload"]["workdir"] == str(workdir)

    def test_watch_lock_defers_until_released(self, make_runtime):
        class FakeT1:
            def run(self, code: str, *, workdir: Path) -> T1Outcome:
                workdir.mkdir(parents=True, exist_ok=True)
                (workdir / "context-pack.md").write_text("# pack", encoding="utf-8")
                return T1Outcome(ok=True, workdir=workdir, reasons=())

        rt = make_runtime(t1=FakeT1())
        from stock_trading_agent.loop.monitor_events import parse_line

        rt.record_monitor_event(parse_line(_line("600519")))
        rt.layout.monitor_dir.mkdir(parents=True, exist_ok=True)
        rt.layout.watch_lock.touch()
        assert rt.consume_one() is None  # defer：盯盘中不跑取数（I-005）
        q = json.loads(rt.layout.queue_path.read_text(encoding="utf-8"))
        assert q["tasks"][0]["status"] == "queued"
        rt.layout.watch_lock.unlink()
        task = rt.consume_one()  # 收盘锁释放自动补跑
        assert task is not None and task.status == "done"

    def test_per_code_per_day_once_even_after_done(self, make_runtime):
        class FakeT1:
            def run(self, code: str, *, workdir: Path) -> T1Outcome:
                return T1Outcome(ok=True, workdir=workdir, reasons=())

        rt = make_runtime(t1=FakeT1())
        from stock_trading_agent.loop.monitor_events import parse_line

        rt.record_monitor_event(parse_line(_line("600519")))
        assert rt.consume_one().status == "done"
        # 当日再触发（新事件行）：done 终态命中 find_any → 不再入队
        assert rt.record_monitor_event(parse_line(_line("600519"))) is None
