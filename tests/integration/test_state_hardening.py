"""状态层健壮性回归（0.0.15，I-003 修复族）：W1-W5/W7 + G1 归档端到端（tmp 状态目录）。

W4 主回归：跨 daemon 重启（acquire→consume→stop→acquire→consume）后盘上 decisions.jsonl
无重复 seq 且 verify_chain 通过——这正是生产破链（57 行 46 重复）的场景钉死。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from stock_trading_agent.cc_client.dryrun import DryRunCCClient
from stock_trading_agent.loop.worker import LoopRuntime
from stock_trading_agent.paper.confirm import request_from_fill
from stock_trading_agent.paper.fills import Fill
from stock_trading_agent.paper.pending import PendingQueue
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.prune import archive_state
from stock_trading_agent.state.store import atomic_write_json


def _runtime(layout: StateLayout) -> LoopRuntime:
    return LoopRuntime(layout=layout, cc=DryRunCCClient(audit_dir=layout.audit_dir))


def _write_decisions(path: Path, log: DecisionLog) -> None:
    text = "".join(json.dumps(asdict(line), ensure_ascii=False) + "\n" for line in log.lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestCursorRegression:
    def test_no_duplicate_seq_across_daemon_restart(self, tmp_path: Path):
        """W4：双代 daemon（模拟生产破链场景）后盘上链完整。"""
        layout = StateLayout(root=tmp_path / "state")
        runtime = _runtime(layout)
        assert runtime.acquire_daemon()
        runtime.submit(kind="diagnose_quick", code="600519", source="panel")
        runtime.consume_one()
        runtime.stop()

        runtime2 = _runtime(layout)  # 第二代 daemon：换入盘上链
        assert runtime2.acquire_daemon()
        # 异 code：防同秒内 task_id（dedup_key+HHMMSS）撞车
        runtime2.submit(kind="diagnose_quick", code="601919", source="panel")
        runtime2.consume_one()
        runtime2._decide("l2_toggled", {"enabled": False})  # 旁路写点族（W1 已收编）
        runtime2.stop()

        chain = DecisionLog.load(layout.decisions_path)  # strict=True：破链即抛
        seqs = [line.seq for line in chain.lines]
        assert seqs == list(range(1, len(seqs) + 1))  # 无重复、无跳号
        assert len(seqs) >= 5  # submit×2 + task_done×2 + l2_toggled 至少 5 行

    def test_broken_chain_refuses_daemon(self, tmp_path: Path):
        """W3：破链文件拒载入（serve 降级只读语义）+ 降级防毒重绑（append 不落盘）。"""
        layout = StateLayout(root=tmp_path / "state")
        log = DecisionLog()
        log.append("2026-11-08T00:00:00Z", "task_done", {"t": 1})
        _write_decisions(layout.decisions_path, log)
        raw = layout.decisions_path.read_text(encoding="utf-8")
        layout.decisions_path.write_text(raw + raw.splitlines()[0] + "\n", encoding="utf-8")
        runtime = _runtime(layout)
        assert runtime.acquire_daemon() is False
        assert runtime.is_active is False
        # 降级防毒：decisions 重绑纯内存链，append 不向已有多行文件写 seq=1 毒行
        assert runtime.loop.decisions.path is None
        before = layout.decisions_path.read_text(encoding="utf-8")
        runtime.loop.decisions.append("2026-11-08T01:00:00Z", "l2_probe", {"ok": True})
        assert layout.decisions_path.read_text(encoding="utf-8") == before


class TestRepairCli:
    def test_repair_dryrun_then_apply(self, tmp_path: Path, monkeypatch):
        from stock_trading_agent.__main__ import main

        monkeypatch.setenv("STA_STATE_ROOT", str(tmp_path / "state"))
        layout = StateLayout.from_env()
        log = DecisionLog()
        log.append("2026-11-07T00:00:00Z", "task_done", {"task_id": "t1"})
        log.append("2026-11-07T00:00:01Z", "task_done", {"task_id": "t2"})
        _write_decisions(layout.decisions_path, log)
        raw = layout.decisions_path.read_text(encoding="utf-8")
        broken = raw + raw  # 全史重灌（生产 I-003 形态）
        layout.decisions_path.write_text(broken, encoding="utf-8")

        assert main(["repair-decisions"]) == 0  # dry-run 不改文件
        assert layout.decisions_path.read_text(encoding="utf-8") == broken

        assert main(["repair-decisions", "--apply"]) == 0
        chain = DecisionLog.load(layout.decisions_path)  # strict 通过
        kinds = [line.kind for line in chain.lines]
        assert kinds == ["task_done", "task_done", "chain_repaired"]  # 留痕行在场
        assert chain.lines[-1].payload["dropped_duplicates"] == 2

    def test_repair_refuses_unparseable_line(self, tmp_path: Path, monkeypatch):
        """坏行守护：无法解析的行 → 报行号 exit 4，不写任何东西。"""
        from stock_trading_agent.__main__ import main

        monkeypatch.setenv("STA_STATE_ROOT", str(tmp_path / "state"))
        layout = StateLayout.from_env()
        log = DecisionLog()
        log.append("2026-11-07T00:00:00Z", "task_done", {"t": 1})
        _write_decisions(layout.decisions_path, log)
        raw = layout.decisions_path.read_text(encoding="utf-8")
        broken = raw + '{"truncated' + "\n"
        layout.decisions_path.write_text(broken, encoding="utf-8")

        assert main(["repair-decisions", "--apply"]) == 4
        assert layout.decisions_path.read_text(encoding="utf-8") == broken  # 拒碰原文件


class TestPaperOrphanPurge:
    def test_confirmed_residue_purged_with_decision(self, tmp_path: Path):
        """W5b：confirmed 残留启动自愈 + paper_orphan_purged 留痕。"""
        layout = StateLayout(root=tmp_path / "state")
        fill = Fill(
            code="600519", side="buy", lots=1, price=9.6, date="2026-11-08", tranche_leg="T2_limit"
        )
        req = request_from_fill(fill, request_id="pf-1", ref_price=9.6, ts="t")
        queue = PendingQueue().enqueue(req).confirm("pf-1")  # 崩溃窗口残留态
        atomic_write_json(layout.sim_pending_fills_path, queue.to_dict())

        runtime = _runtime(layout)
        assert runtime.acquire_daemon()
        purged = runtime.purge_confirmed_orphans()
        runtime.stop()

        assert [p["request_id"] for p in purged] == ["pf-1"]
        from stock_trading_agent.paper.pending import load as pending_load

        assert pending_load(layout.sim_pending_fills_path).requests == []
        chain = DecisionLog.load(layout.decisions_path)
        assert chain.lines[-1].kind == "paper_orphan_purged"


class TestArchiveState:
    def test_six_bucket_archive_apply(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        # queue：旧终态 + 新终态 + 活动任务
        tasks = [
            {
                "task_id": "old",
                "dedup_key": "k1",
                "kind": "diagnose_quick",
                "code": "600519",
                "status": "done",
                "enqueued_at": "2026-09-01T01:00:00+00:00",
                "finished_at": "2026-09-01T02:00:00+00:00",
            },
            {
                "task_id": "fresh",
                "dedup_key": "k2",
                "kind": "diagnose_quick",
                "code": "600519",
                "status": "done",
                "enqueued_at": "2026-11-08T01:00:00+00:00",
                "finished_at": "2026-11-08T02:00:00+00:00",
            },
            {
                "task_id": "live",
                "dedup_key": "k3",
                "kind": "diagnose_quick",
                "code": "600519",
                "status": "queued",
                "enqueued_at": "2026-11-08T03:00:00+00:00",
            },
        ]
        atomic_write_json(layout.queue_path, {"version": 1, "tasks": tasks})
        sessions = {
            "u1": {"session_uuid": "u1", "task_id": "old", "ts": "2026-08-01T00:00:00+00:00"},
            "u2": {"session_uuid": "u2", "task_id": "fresh", "ts": "2026-11-08T00:00:00+00:00"},
        }
        atomic_write_json(layout.sessions_path, {"version": 1, "sessions": sessions})
        atomic_write_json(
            layout.budget_path,
            {
                "daily": {"2026-04-01": {"usd": 1.0}, "2026-11-08": {"usd": 2.0}},
                "monthly": {"2026-11": {}},
            },
        )
        log = DecisionLog()
        log.append("2026-09-30T23:00:00+00:00", "task_done", {"m": "old"})  # 超尾随保底窗（>14 天）
        log.append("2026-11-08T10:00:00+00:00", "task_done", {"m": "new"})
        _write_decisions(layout.decisions_path, log)
        audit_old = layout.audit_dir / "2026-07-01" / "s.jsonl"  # >90 天窗
        audit_old.parent.mkdir(parents=True, exist_ok=True)
        audit_old.write_text('{"x":1}\n', encoding="utf-8")
        runs_old = layout.runs_dir / "2026-10-01"
        runs_old.mkdir(parents=True)
        (runs_old / "evidence.json").write_text("{}", encoding="utf-8")
        old_ts = os.stat(runs_old).st_mtime - 90 * 86400
        os.utime(runs_old, (old_ts, old_ts))

        dry_summary = archive_state(layout, today="2026-11-08", apply=False)
        assert dry_summary["audit_files"] == {
            "archive": 1,
            "keep": 0,
        }  # 六桶全量预览（dry-run 不漏）
        assert dry_summary["runs_dirs"] == {"archive": 1, "keep": 0}

        summary = archive_state(layout, today="2026-11-08", apply=True)

        assert summary["queue"] == {"archive": 1, "keep": 2}
        assert summary["sessions"]["archive"] == 1
        assert summary["budget_daily"]["archive"] == 1
        assert summary["decisions"]["archive"] == 1
        assert summary["audit_files"]["archive"] == 1
        assert summary["runs_dirs"]["archive"] == 1

        from stock_trading_agent.state.store import read_json

        kept_tasks = read_json(layout.queue_path, expect_version=1)["tasks"]
        assert [t["task_id"] for t in kept_tasks] == ["fresh", "live"]
        assert (layout.agent_dir / "archive" / "queue-2026-09.jsonl").exists()
        assert (layout.agent_dir / "archive" / "sessions-2026-08.jsonl").exists()
        assert (layout.agent_dir / "archive" / "budget-daily-2026-04.jsonl").exists()
        assert (layout.agent_dir / "archive" / "decisions-2026-09.jsonl").exists()
        assert (layout.agent_dir / "archive" / "audit" / "2026-07-01" / "s.jsonl.gz").exists()
        assert (layout.agent_dir / "archive" / "runs" / "2026-10-01.tgz").exists()
        assert not audit_old.exists() and not runs_old.exists()

        chain = DecisionLog.load(layout.decisions_path)  # 热文件重建链 + 留痕
        kinds = [line.kind for line in chain.lines]
        assert kinds == ["task_done", "state_archived"]
        assert chain.lines[0].payload == {"m": "new"}  # 保留行 payload 不动、seq 重排

    def test_month_start_row_within_lookback_window_kept(self, tmp_path: Path):
        """尾随保底窗：月初跑归档，上一月但仍在外仓 T+5 回看窗内的行恒留热文件。"""
        layout = StateLayout(root=tmp_path / "state")
        log = DecisionLog()
        log.append("2026-11-27T23:00:00+00:00", "advice_card_written", {"code": "600519"})
        log.append("2026-11-28T10:00:00+00:00", "task_done", {"m": "new"})
        _write_decisions(layout.decisions_path, log)

        summary = archive_state(layout, today="2026-12-01", apply=True)

        assert summary["decisions"] == {"archive": 0, "keep": 2}
        chain = DecisionLog.load(layout.decisions_path)
        assert [line.kind for line in chain.lines] == ["advice_card_written", "task_done"]
        assert not (layout.agent_dir / "archive").exists() or not any(
            (layout.agent_dir / "archive").iterdir()
        )  # 零归档零落盘

    def test_unparseable_row_archived_not_crash(self, tmp_path: Path):
        """坏行不再中断 apply：随归档迁出（保档案），热链照常重建。"""
        layout = StateLayout(root=tmp_path / "state")
        log = DecisionLog()
        log.append("2026-09-30T23:00:00+00:00", "task_done", {"m": "old"})
        log.append("2026-11-08T10:00:00+00:00", "task_done", {"m": "new"})
        _write_decisions(layout.decisions_path, log)
        raw = layout.decisions_path.read_text(encoding="utf-8")
        layout.decisions_path.write_text(raw + '{"truncated' + "\n", encoding="utf-8")

        summary = archive_state(layout, today="2026-11-08", apply=True)

        assert summary["decisions"] == {"archive": 2, "keep": 1}
        # 坏行不可解析 → 回落当月桶（2026-11），上月行进上月桶；均留档案原样
        assert (layout.agent_dir / "archive" / "decisions-2026-09.jsonl").read_text(
            encoding="utf-8"
        ).count("\n") == 1
        assert (layout.agent_dir / "archive" / "decisions-2026-11.jsonl").read_text(
            encoding="utf-8"
        ).splitlines() == ['{"truncated']
        chain = DecisionLog.load(layout.decisions_path)  # strict 通过：坏行不在热文件
        assert [line.kind for line in chain.lines] == ["task_done", "state_archived"]

    def test_dryrun_touches_nothing(self, tmp_path: Path):
        layout = StateLayout(root=tmp_path / "state")
        log = DecisionLog()
        log.append("2026-09-30T23:00:00+00:00", "task_done", {"m": "old"})
        _write_decisions(layout.decisions_path, log)
        before = layout.decisions_path.read_text(encoding="utf-8")
        summary = archive_state(layout, today="2026-11-08", apply=False)
        assert summary["decisions"]["archive"] == 1
        assert layout.decisions_path.read_text(encoding="utf-8") == before
