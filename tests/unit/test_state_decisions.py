"""decision log hash 链：追加、防篡改、防删除。"""

from __future__ import annotations

from pathlib import Path

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
