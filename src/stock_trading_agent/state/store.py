"""落盘单一入口：原子写（tmp+fsync+os.replace）、flock、schema 版本闸。

方案 §10⑤（原子 os.replace 写 agent/）与 §13（数据面单文件 JSON 原子写、不引数据库）。
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

CURRENT_SCHEMA_VERSION = 1


class SchemaVersionMismatch(RuntimeError):
    """读到未知 schema 版本：拒读、拒静默迁移（显式报错，防两代写者互相覆盖）。"""


def read_json(path: Path, *, expect_version: int | None = None) -> dict | None:
    """读 JSON；文件不存在返回 None；版本闸：版本不符即拒读（显式报错，不静默迁移）。"""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if expect_version is not None:
        version = data.get("version")
        if version != expect_version:
            raise SchemaVersionMismatch(
                f"{path}: schema 版本 {version!r} != 期望 {expect_version}——拒读，须显式迁移"
            )
    return data


def atomic_write_json(path: Path, payload: dict, *, makedirs: bool = True) -> None:
    """原子写 JSON：同目录 tmp + fsync + os.replace（崩溃旧文件完整或新文件完整，无半写态）。"""
    if makedirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # 失败清理半写 tmp（旧文件保持完整）
        raise


def atomic_append_jsonl(path: Path, obj: dict, *, makedirs: bool = True) -> None:
    """追加写 JSONL（文件级 flock 串行；追加前 fsync 保证断电不丢尾行）。"""
    if makedirs:
        path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class LockTimeout(RuntimeError):
    """flock 超时（daemon.lock 单实例 / 串行写竞争）。"""


@contextlib.contextmanager
def FileLock(path: Path, *, timeout_s: float = 1.0) -> Iterator[None]:
    """fcntl flock 互斥锁（macOS/Linux；Windows 非目标平台，AGENTS.md 已声明）。

    用途：agent/daemon.lock 单实例、跨进程串行写；坑位：锁文件不删除（避免删除-重建竞态）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w")
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"flock 超时：{path}") from exc
                time.sleep(0.02)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
