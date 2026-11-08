"""状态归档（G1 六桶 retention；归档非删除——热数据钉常数规模，档案可 cat/jq 复原）。

决策依据 [storage-db-decision.md](../../docs/research/storage-db-decision.md) §3/§6；
CLI `archive-state` 默认 dry-run，--apply 才落盘，且要求 daemon 空闲（单写者纪律）。
档案统一落 agent/archive/<桶>-YYYYMM.jsonl（月度 append 复用 store.atomic_append_lines）；
audit 按日期目录 gzip 迁档；runs/ 按目录 mtime tar.gz 迁档。
坑位：decisions 月度切分已核实跨仓契约安全（外仓 agent_postmortem.py 硬编码单文件 +
T+5 回看 ≪ 当月窗口）；归档后面板读侧只看热文件，跨月检索走档案 jq（T5 判据数据源）。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict

from stock_trading_agent.state.decisions import DecisionLine, DecisionLog, plan_repair
from stock_trading_agent.state.paths import StateLayout
from stock_trading_agent.state.store import (
    atomic_append_lines,
    atomic_write_json,
    atomic_write_text,
)

TERMINAL_STATES = ("done", "failed", "abandoned")
QUEUE_KEEP_DAYS = 30
QUEUE_KEEP_MAX = 500
SESSIONS_KEEP_DAYS = 90
AUDIT_KEEP_DAYS = 90
RUNS_KEEP_DAYS = 14
BUDGET_DAILY_KEEP_DAYS = 180


def _date_str(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value).strftime("%Y-%m-%d")
    except ValueError:
        return str(value)[:10] or None


def _days_old(day: str | None, today: str) -> int:
    """day 距 today 天数（不可解析 → -1 视为「保守保留」）。"""
    if day is None:
        return -1
    try:
        delta = _dt.date.fromisoformat(today) - _dt.date.fromisoformat(day[:10])
    except ValueError:
        return -1
    return delta.days


def split_queue(
    tasks: list[dict],
    *,
    today: str,
    keep_days: int = QUEUE_KEEP_DAYS,
    keep_max: int = QUEUE_KEEP_MAX,
) -> tuple[list[dict], list[dict]]:
    """queue 终态任务分桶：非终态恒留；终态按「最近 keep_max 条 ∧ 未超 keep_days 天」保留。

    坑位：时间锚优先 finished_at 回落 enqueued_at（两者皆缺/不可解析 → 保守保留）。
    """
    terminal = [t for t in tasks if t.get("status") in TERMINAL_STATES]

    def _recency(t: dict) -> str:
        return str(t.get("finished_at") or t.get("enqueued_at") or "")

    terminal.sort(key=_recency, reverse=True)
    archive_rank = {id(t) for t in terminal[keep_max:]}
    keep: list[dict] = []
    archive: list[dict] = []
    for t in tasks:
        if t.get("status") not in TERMINAL_STATES:
            keep.append(t)
            continue
        day = _date_str(t.get("finished_at") or t.get("enqueued_at"))
        if id(t) in archive_rank or _days_old(day, today) > keep_days:
            archive.append(t)
        else:
            keep.append(t)
    return keep, archive


def split_sessions(
    sessions: dict[str, dict], *, today: str, keep_days: int = SESSIONS_KEEP_DAYS
) -> tuple[dict[str, dict], list[dict]]:
    """sessions 分桶（时间锚 ts）：返回 (保留 dict, 归档 rows)。"""
    keep: dict[str, dict] = {}
    archive: list[dict] = []
    for key, row in sessions.items():
        if _days_old(_date_str(str(row.get("ts", ""))), today) > keep_days:
            archive.append(row)
        else:
            keep[key] = row
    return keep, archive


def budget_daily_split(
    ledger: dict, *, today: str, keep_days: int = BUDGET_DAILY_KEEP_DAYS
) -> tuple[dict, dict[str, dict]]:
    """budget daily 桶分桶：返回 (保留 daily, 归档 daily)；monthly 全期保留。"""
    keep: dict[str, dict] = {}
    archive: dict[str, dict] = {}
    for day, bucket in (ledger.get("daily") or {}).items():
        (archive if _days_old(str(day), today) > keep_days else keep)[str(day)] = bucket
    return keep, archive


def split_decisions_rows(rows: list[str], *, today: str) -> tuple[list[str], list[str]]:
    """decisions 月度切分：ts 月 < 当月的行归档（跨仓契约已核实安全）。不可解析行保守保留。"""
    keep: list[str] = []
    archive: list[str] = []
    for raw in rows:
        try:
            ts = str(json.loads(raw).get("ts", ""))
        except ValueError:
            keep.append(raw)
            continue
        (archive if ts[:7] < today[:7] else keep).append(raw)
    return keep, archive


def _rebuild_live_decisions(kept_rows: list[str]) -> list[DecisionLine]:
    """热文件链重建：保留行 payload 不动、seq 重排、hash 全链重算（归档行留在档案原样）。"""
    kept_lines = [DecisionLine(**json.loads(raw)) for raw in kept_rows]
    rebuilt, _ = plan_repair(kept_lines)
    return rebuilt


def archive_state(layout: StateLayout, *, today: str, apply: bool) -> dict:
    """六桶归档主口：默认 dry-run（只读盘点返回计划），apply=True 落盘。

    返回摘要 {bucket: {"archive": n, "keep": n}}；apply 后 decisions 追加 state_archived 留痕。
    坑位：调用方须先确认 daemon 空闲（CLI 已做 FileLock 守卫）；本函数自身不再抢锁。
    """
    summary: dict[str, dict] = {}
    archive_dir = layout.agent_dir / "archive"

    # --- queue / sessions / budget / decisions（台账四桶） ---
    from stock_trading_agent.state.store import read_json

    queue_data = read_json(layout.queue_path, expect_version=1) or {"tasks": []}
    keep_tasks, arch_tasks = split_queue(list(queue_data.get("tasks", [])), today=today)
    summary["queue"] = {"archive": len(arch_tasks), "keep": len(keep_tasks)}

    sessions_data = read_json(layout.sessions_path, expect_version=1) or {"sessions": {}}
    keep_sessions, arch_sessions = split_sessions(
        dict(sessions_data.get("sessions") or {}), today=today
    )
    summary["sessions"] = {"archive": len(arch_sessions), "keep": len(keep_sessions)}

    budget_data = read_json(layout.budget_path) or {}
    keep_daily, arch_daily = budget_daily_split(budget_data, today=today)
    summary["budget_daily"] = {"archive": len(arch_daily), "keep": len(keep_daily)}

    from stock_trading_agent.state.store import read_jsonl_lines_cached

    decision_rows = read_jsonl_lines_cached(layout.decisions_path)
    keep_rows, arch_rows = split_decisions_rows(decision_rows, today=today)
    summary["decisions"] = {"archive": len(arch_rows), "keep": len(keep_rows)}

    if not apply:
        return summary

    def _month_of(row: dict | str) -> str:
        raw = json.loads(row) if isinstance(row, str) else row
        # 时间锚链：ts（决策/会话）→ finished_at → enqueued_at（任务）；全缺回落当月
        for key in ("ts", "finished_at", "enqueued_at"):
            if raw.get(key):
                return str(raw[key])[:7]
        return today[:7]

    # queue 档案（按 finished/enqueued 月分文件）
    for row in arch_tasks:
        month = _month_of(row)[:7]
        atomic_append_lines(
            archive_dir / f"queue-{month}.jsonl", [json.dumps(row, ensure_ascii=False)]
        )
    if arch_tasks:
        atomic_write_json(layout.queue_path, {**queue_data, "tasks": keep_tasks})

    for row in arch_sessions:
        atomic_append_lines(
            archive_dir / f"sessions-{_month_of(row)}.jsonl", [json.dumps(row, ensure_ascii=False)]
        )
    if arch_sessions:
        atomic_write_json(layout.sessions_path, {**sessions_data, "sessions": keep_sessions})

    for day, bucket in sorted(arch_daily.items()):
        atomic_append_lines(
            archive_dir / f"budget-daily-{day[:7]}.jsonl",
            [json.dumps({"day": day, **bucket}, ensure_ascii=False)],
        )
    if arch_daily:
        atomic_write_json(layout.budget_path, {**budget_data, "daily": keep_daily})

    for row in arch_rows:
        atomic_append_lines(archive_dir / f"decisions-{_month_of(row)}.jsonl", [row])
    if arch_rows:
        rebuilt = _rebuild_live_decisions(keep_rows)
        text = "".join(json.dumps(asdict(line), ensure_ascii=False) + "\n" for line in rebuilt)
        atomic_write_text(layout.decisions_path, text)
        log = DecisionLog(rebuilt, path=layout.decisions_path)
        log.append(
            _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds"),
            "state_archived",
            {"decisions_archived": len(arch_rows), "note": "月度切分归档，档案 agent/archive/"},
        )

    # --- audit（gzip 迁档）/ runs（mtime tar 迁档） ---
    import gzip
    import shutil
    import tarfile

    audit_archived = runs_archived = 0
    cutoff_runs = (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=RUNS_KEEP_DAYS)).timestamp()
    if layout.audit_dir.exists():
        for day_dir in sorted(layout.audit_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            if _days_old(day_dir.name, today) <= AUDIT_KEEP_DAYS:
                continue
            for src in sorted(day_dir.glob("*.jsonl")):
                dest = archive_dir / "audit" / day_dir.name / (src.name + ".gz")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with src.open("rb") as fin, gzip.open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                src.unlink()
                audit_archived += 1
            if not any(day_dir.iterdir()):
                day_dir.rmdir()
    summary["audit_files"] = {"archive": audit_archived, "keep": -1}
    if layout.runs_dir.exists():
        for entry in sorted(layout.runs_dir.iterdir()):
            try:
                if entry.stat().st_mtime > cutoff_runs:
                    continue
            except OSError:
                continue
            dest = archive_dir / "runs" / f"{entry.name}.tgz"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(dest, "w:gz") as tar:
                tar.add(entry, arcname=entry.name)
            shutil.rmtree(entry, ignore_errors=True)
            runs_archived += 1
    summary["runs_dirs"] = {"archive": runs_archived, "keep": -1}
    return summary
