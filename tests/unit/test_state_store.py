"""原子写、flock 与 schema 版本闸。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_trading_agent.state.store import (
    FileLock,
    LockTimeout,
    SchemaVersionMismatch,
    atomic_write_json,
    read_json,
)


class TestAtomicWrite:
    def test_write_and_read_roundtrip(self, tmp_path: Path):
        p = tmp_path / "a" / "f.json"
        atomic_write_json(p, {"version": 1, "x": 1})
        assert read_json(p, expect_version=1) == {"version": 1, "x": 1}

    def test_no_tmp_residue(self, tmp_path: Path):
        p = tmp_path / "f.json"
        atomic_write_json(p, {"version": 1})
        assert list(tmp_path.iterdir()) == [p]

    def test_failure_keeps_old_file_and_cleans_tmp(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "f.json"
        atomic_write_json(p, {"version": 1, "v": "old"})

        def boom(*_a, **_k):
            raise OSError("disk on fire")

        monkeypatch.setattr("stock_trading_agent.state.store.os.replace", boom)
        with pytest.raises(OSError):
            atomic_write_json(p, {"version": 1, "v": "new"})
        assert read_json(p)["v"] == "old"  # 旧文件完整（无半写态）
        assert not list(tmp_path.glob(".*tmp"))  # 失败清理半写 tmp

    def test_version_gate_refuses_unknown(self, tmp_path: Path):
        p = tmp_path / "f.json"
        atomic_write_json(p, {"version": 99})
        with pytest.raises(SchemaVersionMismatch):
            read_json(p, expect_version=1)  # 拒读拒静默迁移


class TestFileLock:
    def test_second_acquire_times_out(self, tmp_path: Path):
        lock = tmp_path / "daemon.lock"
        with FileLock(lock, timeout_s=0.05):
            with pytest.raises(LockTimeout):
                with FileLock(lock, timeout_s=0.05):
                    pass

    def test_reentrant_after_release(self, tmp_path: Path):
        lock = tmp_path / "daemon.lock"
        with FileLock(lock, timeout_s=0.5):
            pass
        with FileLock(lock, timeout_s=0.5):
            pass  # 释放后可重入

    def test_sequential_writes_last_wins(self, tmp_path: Path):
        # 顺序写：两次原子写最终态一致（后写胜，无混合）；真并发互斥由 flock 用例覆盖
        p = tmp_path / "q.json"
        atomic_write_json(p, {"version": 1, "n": 1})
        atomic_write_json(p, {"version": 1, "n": 2})
        assert json.loads(p.read_text())["n"] == 2

    def test_atomic_append_jsonl_lines_and_trailing_newline(self, tmp_path: Path):
        from stock_trading_agent.state.store import atomic_append_jsonl

        p = tmp_path / "log.jsonl"
        atomic_append_jsonl(p, {"seq": 1})
        atomic_append_jsonl(p, {"seq": 2})
        raw = p.read_text(encoding="utf-8")
        assert raw.endswith("\n") and len(raw.splitlines()) == 2
        assert [json.loads(line)["seq"] for line in raw.splitlines()] == [1, 2]

    def test_atomic_append_serialized_under_lock(self, tmp_path: Path):
        from stock_trading_agent.state.store import FileLock, atomic_append_jsonl

        p = tmp_path / "log.jsonl"
        with FileLock(p.with_suffix(".lock"), timeout_s=1.0):
            # 持锁期间同文件 append 仍可完成（append 自带 flock 排队，不死锁不丢行）
            atomic_append_jsonl(p, {"seq": 1})
        atomic_append_jsonl(p, {"seq": 2})
        assert len(p.read_text(encoding="utf-8").splitlines()) == 2
