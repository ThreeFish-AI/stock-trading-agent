"""状态归档（G1 六桶 retention；归档非删除——热数据钉常数规模，档案可 cat/jq 复原）。

决策依据 [storage-db-decision.md](../../docs/research/storage-db-decision.md) §3/§6；
CLI `archive-state` 默认 dry-run，--apply 才落盘，且要求 daemon 空闲（单写者纪律）。
档案统一落 agent/archive/<桶>-YYYYMM.jsonl（月度 append 复用 store.atomic_append_lines）；
audit 按日期目录 gzip 迁档；runs/ 按目录 mtime tar.gz 迁档。
坑位：decisions 月度切分带尾随保底窗（月初跨月界仍覆盖外仓 agent_postmortem.py 的
T+5 交易日回看）；归档后面板读侧只看热文件，跨月检索走档案 jq（T5 判据数据源）。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict
from pathlib import Path

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
# decisions 尾随保底窗：月初跨月界也须覆盖外仓 T+5 回看（5 交易日 + 节假日最坏 ~14 自然日）
DECISIONS_KEEP_RECENT_DAYS = 14


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
    """decisions 月度切分：ts 月 < 当月 ∧ 超出尾随保底窗的行归档。

    坑位：尾随保底窗（DECISIONS_KEEP_RECENT_DAYS）覆盖月初跨月界——外仓 agent_postmortem
    只读热文件、T+1/3/5 回看最长 5 个交易日（节假日最坏 ~14 个自然日），纯月前缀比较会在
    月初把仍在回看窗内的上一月行移出热文件，T+3/T+5 后验被静默跳过。不可解析行随归档
    迁出（保档案不丢；留在 keep 会让 _rebuild_live_decisions 在 apply 中途崩溃）。
    """
    keep: list[str] = []
    archive: list[str] = []
    for raw in rows:
        try:
            ts = str(json.loads(raw).get("ts", ""))
        except ValueError:
            archive.append(raw)
            continue
        old_month = ts[:7] < today[:7]
        beyond_window = _days_old(_date_str(ts), today) > DECISIONS_KEEP_RECENT_DAYS
        (archive if old_month and beyond_window else keep).append(raw)
    return keep, archive


def _rebuild_live_decisions(kept_rows: list[str]) -> list[DecisionLine]:
    """热文件链重建：保留行 payload 不动、seq 重排、hash 全链重算（归档行留在档案原样）。"""
    kept_lines = [DecisionLine(**json.loads(raw)) for raw in kept_rows]
    rebuilt, _ = plan_repair(kept_lines)
    return rebuilt


def archive_state(layout: StateLayout, *, today: str, apply: bool) -> dict:
    """六桶归档主口：默认 dry-run（只读盘点返回计划），apply=True 落盘。

    返回摘要 {bucket: {"archive": n, "keep": n}}（audit/runs 亦在 dry-run 统计——六桶全量预览，
    「只打印计划」不得漏报两个迁移桶）；apply 后 decisions 追加 state_archived 留痕。
    坑位：调用方须先确认 daemon 空闲（CLI 已做 FileLock 守卫）；本函数自身不再抢锁。
    """
    summary: dict[str, dict] = {}
    archive_dir = layout.agent_dir / "archive"

    def _month_of(row: dict | str) -> str:
        try:
            raw = json.loads(row) if isinstance(row, str) else row
        except ValueError:  # 不可解析行（随归档迁出）：回落当月桶
            return today[:7]
        # 时间锚链：ts（决策/会话）→ finished_at → enqueued_at（任务）；全缺回落当月
        for key in ("ts", "finished_at", "enqueued_at"):
            if raw.get(key):
                return str(raw[key])[:7]
        return today[:7]

    def _batch_append(rows: list[str], prefix: str, month_of=None) -> None:
        """按月聚合成单次批量追加（坑位：逐行 append 的中断-重跑窗口随行数放大，档案会双份）。"""
        month_of = month_of or (lambda row: _month_of(row)[:7])
        by_month: dict[str, list[str]] = {}
        for row in rows:
            by_month.setdefault(month_of(row), []).append(row)
        for month, batch in sorted(by_month.items()):
            atomic_append_lines(archive_dir / f"{prefix}-{month}.jsonl", batch)

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

    # --- audit / runs（先扫描统计——dry-run 同样可见；apply 再迁移） ---
    import gzip
    import shutil
    import tarfile

    audit_plan: list[tuple[str, Path]] = []  # (day, src jsonl)
    audit_total = 0
    if layout.audit_dir.exists():
        for day_dir in sorted(layout.audit_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            files = sorted(day_dir.glob("*.jsonl"))
            audit_total += len(files)
            if _days_old(day_dir.name, today) > AUDIT_KEEP_DAYS:
                audit_plan.extend((day_dir.name, src) for src in files)
    summary["audit_files"] = {"archive": len(audit_plan), "keep": audit_total - len(audit_plan)}

    runs_plan: list[Path] = []
    runs_total = 0
    cutoff_runs = (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=RUNS_KEEP_DAYS)).timestamp()
    if layout.runs_dir.exists():
        for entry in sorted(layout.runs_dir.iterdir()):
            runs_total += 1
            try:
                if entry.stat().st_mtime <= cutoff_runs:
                    runs_plan.append(entry)
            except OSError:
                continue
    summary["runs_dirs"] = {"archive": len(runs_plan), "keep": runs_total - len(runs_plan)}

    if not apply:
        return summary

    # queue 档案（按 finished/enqueued 月分文件，单月单次批量追加）
    if arch_tasks:
        _batch_append([json.dumps(row, ensure_ascii=False) for row in arch_tasks], "queue")
        atomic_write_json(layout.queue_path, {**queue_data, "tasks": keep_tasks})

    if arch_sessions:
        _batch_append([json.dumps(row, ensure_ascii=False) for row in arch_sessions], "sessions")
        atomic_write_json(layout.sessions_path, {**sessions_data, "sessions": keep_sessions})

    if arch_daily:
        _batch_append(
            [
                json.dumps({"day": day, **bucket}, ensure_ascii=False)
                for day, bucket in sorted(arch_daily.items())
            ],
            "budget-daily",
            month_of=lambda row: json.loads(row)["day"][:7],  # budget 行时间锚=day 键
        )
        atomic_write_json(layout.budget_path, {**budget_data, "daily": keep_daily})

    if arch_rows:
        _batch_append(arch_rows, "decisions")
        rebuilt = _rebuild_live_decisions(keep_rows)
        text = "".join(json.dumps(asdict(line), ensure_ascii=False) + "\n" for line in rebuilt)
        atomic_write_text(layout.decisions_path, text)
        DecisionLog(rebuilt).append(
            _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds"),
            "state_archived",
            {"decisions_archived": len(arch_rows), "note": "月度切分归档，档案 agent/archive/"},
            path=layout.decisions_path,  # 显式 path 才走锁内尾对账落盘（唯一入口语义）
        )

    for day, src in audit_plan:
        dest = archive_dir / "audit" / day / (src.name + ".gz")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as fin, gzip.open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        src.unlink()
        if not any(src.parent.iterdir()):
            src.parent.rmdir()
    for entry in runs_plan:
        dest = archive_dir / "runs" / f"{entry.name}.tgz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(entry, arcname=entry.name)
        shutil.rmtree(entry, ignore_errors=True)
    return summary
