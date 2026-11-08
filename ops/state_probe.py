#!/usr/bin/env python3
"""状态根巡检（G2）：三源分列体量/行数 + 触发判据 T1/T3 状态——决策记录 §4 的数据源。

stdlib 直跑零依赖（python3 ops/state_probe.py 或 uv run python ops/state_probe.py），只读
零写入；--json 出机读形态。阈值与 docs/research/storage-db-decision.md §4 同源（判据只收严：
改阈值须先改决策记录再同步此处）。坑位：外仓产物只统计不治理（本仓零接触）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

T1_DECISIONS_LINES = 100_000  # 单月热文件行数触发线
T1_DECISIONS_BYTES = 30 * 1024 * 1024
T3_FILE_COUNT = 5_000  # 状态根文件数触发线
T3_TICKS_BYTES = 500 * 1024 * 1024  # ticks 单独盯（最大外部增长源）


def _dir_stats(path: Path) -> tuple[int, int]:
    """(文件数, 总字节)；缺席 → (0, 0)。"""
    if not path.exists():
        return 0, 0
    files = bytes_ = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                files += 1
                bytes_ += p.stat().st_size
        except OSError:
            continue
    return files, bytes_


def _jsonl_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def collect(root: Path, *, today: str) -> dict:
    """只读采集状态根指标（三源分列：reports/_scans、ticks、audit + 台账行数）。"""
    decisions = root / "agent" / "decisions.jsonl"
    scans_files, scans_bytes = _dir_stats(root / "reports" / "_scans")
    reports_files, reports_bytes = _dir_stats(root / "reports")
    reports_files, reports_bytes = reports_files - scans_files, reports_bytes - scans_bytes
    ticks_files, ticks_bytes = _dir_stats(root / "monitor" / "ticks")
    audit_files, audit_bytes = _dir_stats(root / "agent" / "audit")
    total_files, total_bytes = _dir_stats(root)
    return {
        "asof": today,
        "root": str(root),
        "decisions_lines": _jsonl_lines(decisions),
        "decisions_bytes": decisions.stat().st_size if decisions.exists() else 0,
        "events_today_lines": _jsonl_lines(root / "monitor" / "events" / f"{today}.jsonl"),
        "queue_tasks": _queue_tasks(root / "agent" / "queue.json"),
        "sources": {
            "reports_md": {"files": reports_files, "bytes": reports_bytes},
            "reports_scans": {"files": scans_files, "bytes": scans_bytes},
            "monitor_ticks": {"files": ticks_files, "bytes": ticks_bytes},
            "audit": {"files": audit_files, "bytes": audit_bytes},
        },
        "total": {"files": total_files, "bytes": total_bytes},
    }


def _queue_tasks(path: Path) -> int:
    try:
        with path.open(encoding="utf-8") as fh:
            return len(json.load(fh).get("tasks", []))
    except (OSError, ValueError):
        return 0


def trigger_status(metrics: dict) -> dict:
    """T1/T3 判据状态（T2 需端到端计时、T4 需 audit 实测报告、T5 需检索端点计数——不在此采集）。"""
    return {
        "T1_decisions_scale": {
            "hit": metrics["decisions_lines"] > T1_DECISIONS_LINES
            or metrics["decisions_bytes"] > T1_DECISIONS_BYTES,
            "lines": metrics["decisions_lines"],
            "lines_threshold": T1_DECISIONS_LINES,
        },
        "T3_file_count": {
            "hit": metrics["total"]["files"] > T3_FILE_COUNT,
            "files": metrics["total"]["files"],
            "threshold": T3_FILE_COUNT,
        },
        "watch_ticks_growth": {
            "bytes": metrics["sources"]["monitor_ticks"]["bytes"],
            "note": "外仓增长源（本仓零接触），DirWatcher 已 skip 解耦",
        },
    }


def format_text(metrics: dict) -> str:
    src = metrics["sources"]
    rs = src["reports_scans"]
    mt = src["monitor_ticks"]
    lines = [
        f"状态根巡检 asof={metrics['asof']} root={metrics['root']}",
        f"  台账：decisions {metrics['decisions_lines']} 行 / {metrics['decisions_bytes']} B；"
        f"当日 events {metrics['events_today_lines']} 行；queue 任务 {metrics['queue_tasks']}",
        f"  外仓·reports md：{src['reports_md']['files']} 文件 / {src['reports_md']['bytes']} B",
        f"  外仓·reports _scans：{rs['files']} 文件 / {rs['bytes']} B",
        f"  外仓·monitor ticks：{mt['files']} 文件 / {mt['bytes']} B",
        f"  本仓·audit：{src['audit']['files']} 文件 / {src['audit']['bytes']} B",
        f"  合计：{metrics['total']['files']} 文件 / {metrics['total']['bytes']} B",
    ]
    for name, status in trigger_status(metrics).items():
        if "hit" in status:
            flag = "⚠️ 触发" if status["hit"] else "ok"
            lines.append(f"  判据 {name}: {flag} {json.dumps(status, ensure_ascii=False)}")
        else:
            lines.append(f"  观察 {name}: {json.dumps(status, ensure_ascii=False)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="状态根只读巡检（G2，触发判据数据源）")
    parser.add_argument(
        "--root", default=None, help="状态根（缺省 STA_STATE_ROOT 或 ~/.vibe-stock）"
    )
    parser.add_argument("--json", action="store_true", help="机读 JSON 输出")
    args = parser.parse_args(argv)

    import datetime as dt

    default_root = os.environ.get("STA_STATE_ROOT") or str(Path.home() / ".vibe-stock")
    root = Path(args.root or default_root)
    today = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    metrics = collect(root, today=today)
    if args.json:
        payload = {**metrics, "triggers": trigger_status(metrics)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_text(metrics))
    return 0


if __name__ == "__main__":
    sys.exit(main())
