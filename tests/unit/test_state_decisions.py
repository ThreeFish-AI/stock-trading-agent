"""decision log hash 链：追加、防篡改、防删除、尾部对账与保行重链（0.0.15）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_trading_agent.state.decisions import GENESIS, DecisionLog
from stock_trading_agent.state.store import atomic_append_jsonl


class TestChain:
    def test_append_grows_and_links(self):
        log = DecisionLog()
        first = log.append("t1", "task_done", {"task_id": "a"})
        assert first.seq == 1 and first.prev_hash == GENESIS and first.prefix == "agent:"
        second = log.append("t2", "assert_refuse", {"reasons": ["x"]})
        assert second.seq == 2 and second.prev_hash == first.hash
        assert log.verify_chain()

    def test_tamper_payload_detected(self):
        log = DecisionLog()
        log.append("t1", "task_done", {"task_id": "a"})
        log.append("t2", "task_done", {"task_id": "b"})
        log.lines[0].payload["task_id"] = "tampered"
        assert not log.verify_chain()

    def test_delete_line_detected(self):
        log = DecisionLog()
        log.append("t1", "task_done", {"a": 1})
        log.append("t2", "task_done", {"b": 2})
        log.lines.pop(0)  # 删除首行 → seq 断档
        assert not log.verify_chain()

    def test_rewrite_hash_detected(self):
        log = DecisionLog()
        log.append("t1", "task_done", {"a": 1})
        from dataclasses import replace

        forged = replace(log.lines[0], hash="f" * 64)
        log.lines[0] = forged
        assert not log.verify_chain()

    def test_load_verifies_and_appends_to_disk(self, tmp_path: Path):
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog()
        log.append("t1", "task_done", {"a": 1}, path=path)
        log.append("t2", "task_done", {"b": 2}, path=path)
        loaded = DecisionLog.load(path)
        assert len(loaded.lines) == 2 and loaded.verify_chain()
        assert atomic_append_jsonl is not None  # 追加写经 flock 路径（import 冒烟）


class TestTailSyncAppend:
    """0.0.15 写者侧单一入口：锁内尾部对账（W1/W2 防回退）。"""

    def test_append_with_path_syncs_empty_memory_from_file_tail(self, tmp_path: Path):
        path = tmp_path / "decisions.jsonl"
        a = DecisionLog()
        a.append("t1", "task_submitted", {"task_id": "t"}, path=path)
        a.append("t2", "task_done", {"task_id": "t"}, path=path)
        fresh = DecisionLog()  # 空内存实例（重启/降级态）：不得从 seq=1 重写
        line = fresh.append("t3", "task_done", {"task_id": "t"}, path=path)
        assert line.seq == 3 and line.prev_hash == a.lines[-1].hash
        assert DecisionLog.load(path).verify_chain()

    def test_two_log_instances_alternate_appends(self, tmp_path: Path):
        path = tmp_path / "decisions.jsonl"
        a, b = DecisionLog(), DecisionLog()  # 双进程形态（serve 降级态 + 另一写者）
        a.append("t1", "task_submitted", {"task_id": "a"}, path=path)
        b.append("t2", "task_submitted", {"task_id": "b"}, path=path)
        a.append("t3", "task_done", {"task_id": "a"}, path=path)
        loaded = DecisionLog.load(path)
        assert [x.seq for x in loaded.lines] == [1, 2, 3]
        assert loaded.verify_chain()

    def test_memory_behind_file_reloads_strict(self, tmp_path: Path):
        path = tmp_path / "decisions.jsonl"
        a = DecisionLog()
        a.append("t1", "task_submitted", {"task_id": "a"}, path=path)
        stale = DecisionLog.load(path)
        a.append("t2", "task_done", {"task_id": "a"}, path=path)  # 文件前进到 2 行
        line = stale.append("t3", "task_done", {"task_id": "a"}, path=path)  # 内存落后 1 行
        assert line.seq == 3
        assert DecisionLog.load(path).verify_chain()

    def test_memory_ahead_of_file_raises(self, tmp_path: Path):
        from stock_trading_agent.state.decisions import DecisionChainError

        path = tmp_path / "decisions.jsonl"
        a = DecisionLog()
        a.append("t1", "task_submitted", {"task_id": "a"}, path=path)
        ahead = DecisionLog.load(path)
        ahead.append("t2", "task_done", {"task_id": "x"})  # 仅内存（模拟文件回滚后内存超前）
        with pytest.raises(DecisionChainError):
            ahead.append("t3", "task_done", {"task_id": "x"}, path=path)

    def test_equal_len_diverged_tail_raises(self, tmp_path: Path):
        from stock_trading_agent.state.decisions import DecisionChainError

        path = tmp_path / "decisions.jsonl"
        a = DecisionLog()
        a.append("t1", "task_submitted", {"task_id": "a"}, path=path)
        b = DecisionLog.load(path)
        b.lines[0].payload["task_id"] = "diverged"  # 等长但内容分叉
        with pytest.raises(DecisionChainError):
            b.append("t2", "task_done", {"task_id": "b"}, path=path)


class TestStrictLoad:
    """0.0.15 读侧收严：断链/坏行 raise（W4 防回退）。"""

    def test_load_broken_raises_with_line_index(self, tmp_path: Path):
        from dataclasses import asdict

        from stock_trading_agent.state.decisions import DecisionChainError

        path = tmp_path / "decisions.jsonl"
        log = DecisionLog()
        log.append("t1", "task_submitted", {"task_id": "a"}, path=path)
        log.append("t2", "task_done", {"task_id": "a"}, path=path)
        # 制造 W1 形态断链：追加一个 seq=1 的孤立行
        orphan = DecisionLog()
        orphan.append("t3", "task_done", {"task_id": "z"})
        orphan.lines[0].seq = 1
        from stock_trading_agent.state.decisions import compute_hash

        orphan.lines[0].hash = compute_hash(
            1,
            orphan.lines[0].prev_hash,
            orphan.lines[0].ts,
            "agent:",
            "task_done",
            {"task_id": "z"},
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(orphan.lines[0]), ensure_ascii=False) + "\n")
        with pytest.raises(DecisionChainError, match="第 3 行"):
            DecisionLog.load(path)

    def test_load_bad_json_raises(self, tmp_path: Path):
        from stock_trading_agent.state.decisions import DecisionChainError

        path = tmp_path / "decisions.jsonl"
        path.write_text('{"seq": 1, broken\n', encoding="utf-8")
        with pytest.raises(DecisionChainError, match="不可解析"):
            DecisionLog.load(path)


class TestRepair:
    """一次性保行重链（CLI repair-decisions 的纯函数判据）。"""

    def _broken_raw(self, tmp_path: Path) -> list[str]:
        """构造 W1 产物样本：两段各自从 seq=1/GENESIS 起。"""
        from dataclasses import asdict

        seg1 = DecisionLog()
        seg1.append("t1", "task_submitted", {"task_id": "a"})
        seg1.append("t2", "task_done", {"task_id": "a"})
        seg2 = DecisionLog()
        seg2.append("t3", "task_submitted", {"task_id": "b"})
        seg2.append("t4", "task_done", {"task_id": "b"})
        return [json.dumps(asdict(x), ensure_ascii=False) for x in [*seg1.lines, *seg2.lines]]

    def test_repair_preserves_rows_and_verifies(self, tmp_path: Path):
        from stock_trading_agent.state.decisions import repair_chain_lines
        from stock_trading_agent.state.store import atomic_write_text

        raw = self._broken_raw(tmp_path)
        rows, stats = repair_chain_lines(raw, ts="2026-11-08T00:00:00+00:00", old_sha256="0" * 64)
        assert stats.rows_before == 4 and stats.rows_after == 6  # +started/done 两条
        assert stats.breaks == [3, 4]  # 第二段两行 seq 均回退（W1 两段形态）
        kinds = [r["kind"] for r in rows]
        assert kinds[-2:] == ["chain_repair_started", "chain_repair_done"]
        path = tmp_path / "decisions.jsonl"
        atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        loaded = DecisionLog.load(path)  # strict load 可过
        assert [x.seq for x in loaded.lines] == [1, 2, 3, 4, 5, 6]
        assert loaded.verify_chain()

    def test_repair_started_payload_zero_sensitive(self, tmp_path: Path):
        from stock_trading_agent.state.decisions import repair_chain_lines

        rows, _ = repair_chain_lines(self._broken_raw(tmp_path), ts="t", old_sha256="0" * 64)
        started = rows[-2]
        assert set(started["payload"]) == {"rows_before", "old_sha256", "breaks", "tool"}
        assert started["payload"]["tool"] == "cli:repair-decisions"

    def test_repair_noop_on_valid_chain(self, tmp_path: Path):
        path = tmp_path / "decisions.jsonl"
        log = DecisionLog()
        log.append("t1", "task_done", {"a": 1}, path=path)
        assert DecisionLog.load(path).verify_chain()  # 完好链 load 直接过（CLI noop 前置判据）
