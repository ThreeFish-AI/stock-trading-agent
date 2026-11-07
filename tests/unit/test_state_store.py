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

    def test_failure_keeps_old_file(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "f.json"
        atomic_write_json(p, {"version": 1, "v": "old"})

        def boom(*_a, **_k):
            raise OSError("disk on fire")

        monkeypatch.setattr("stock_trading_agent.state.store.os.replace", boom)
        with pytest.raises(OSError):
            atomic_write_json(p, {"version": 1, "v": "new"})
        assert read_json(p)["v"] == "old"  # 旧文件完整（无半写态）

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

    def test_json_payload_survives_concurrent_writer(self, tmp_path: Path):
        # 串行写者模拟：两次原子写最终态一致（后写胜，无混合）
        p = tmp_path / "q.json"
        atomic_write_json(p, {"version": 1, "n": 1})
        atomic_write_json(p, {"version": 1, "n": 2})
        assert json.loads(p.read_text())["n"] == 2
