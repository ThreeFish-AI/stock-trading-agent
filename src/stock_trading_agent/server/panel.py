"""面板 HTML fragment 渲染（服务端手搓，html.escape 全转义防注入；零模板依赖，§16）。

htmx 消费这些 fragment：常态 180s 轮询、任务卡非终态 5s 快轮询（终态停）。
NR-5：只透出脱敏态数据（monitor events/universe 本身脱敏；sessions/budget 为运营元数据）。
"""

from __future__ import annotations

import html

TERMINAL_STATUSES = frozenset({"done", "failed", "abandoned"})


def _status_pill(status: str) -> str:
    cls = _esc(status)
    return f'<span class="st {cls}">{cls}</span>'


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _badge(text: str, cls: str = "") -> str:
    return f'<span class="badge {cls}">{text}</span>'


def render_status_badges(status: dict) -> str:
    loop = status.get("loop") or {}
    locks = status.get("locks") or {}
    active = bool(loop.get("active"))
    watch = "在场" if locks.get("watch") else "无"
    fetch = "在场" if locks.get("fetch") else "无"
    return (
        _badge(f"loop {'运行中' if active else '只读降级'}", "ok" if active else "warn")
        + _badge(f"queued {int(loop.get('queued', 0))} · running {int(loop.get('running', 0))}")
        + _badge(f"watch.lock {watch}", "warn" if locks.get("watch") else "")
        + _badge(f"fetch.lock {fetch}", "warn" if locks.get("fetch") else "")
    )


def render_slots_row(status: dict) -> str:
    slots = status.get("slots") or {}
    cells = []
    for name in ("monitor", "outlook", "postmortem"):
        info = slots.get(name) or {}
        done = bool(info.get("done"))
        label = "已收口" if done else "未收口/未装"
        cells.append(_badge(f"{_esc(name)} 槽 {label}", "ok" if done else ""))
    return " ".join(cells)


def render_overview(status: dict, events: dict, queue: dict, budget: dict) -> str:
    parts = [
        f'<div class="badges">{render_status_badges(status)}</div>',
        f'<div class="badges">{render_slots_row(status)}</div>',
        "<h3>事件流（尾部）</h3>",
        render_events_table(events, tail=10),
        "<h3>任务队列表态</h3>",
        render_tasks_table(queue),
        "<h3>预算水位</h3>",
        render_budget_line(budget),
    ]
    return "".join(parts)


def render_budget_line(budget: dict) -> str:
    if not isinstance(budget, dict) or "tasks" not in budget:
        return '<p class="muted">预算台账未初始化（默认关，仅记账）。</p>'
    tokens = budget.get("tokens") if isinstance(budget.get("tokens"), dict) else {}
    return (
        f'<p class="muted">模式 {_esc(budget.get("billing_mode", "subscription"))} · '
        f"任务数 {int(budget.get('tasks', 0))} · "
        f"token 累计 {int(tokens.get('total', 0))} · "
        f"等效美元 {budget.get('cost_usd_equiv', 0)}（订阅轨双口径记账，F-34）</p>"
    )


def render_events_table(payload: dict, *, tail: int = 50) -> str:
    events = (payload.get("events") or [])[-tail:]
    if not events:
        return '<p class="muted">暂无盯盘事件（watch.py 台账为空或未运行）。</p>'
    rows = []
    for ev in events:
        code = ev.get("code", "*")
        review = ev.get("review") or ""
        suppressed = bool(ev.get("suppress_reason"))
        action = ""
        if review and not suppressed:
            action = (
                f'<button class="mini" hx-post="/api/panel/submit" '
                f'hx-vals=\'{{"kind": "monitor_review", "code": "{_esc(code)}"}}\' '
                f'hx-target="#submit-result" hx-swap="innerHTML">盯盘复核 {_esc(code)}</button>'
            )
        rows.append(
            "<tr>"
            f"<td>{_esc(ev.get('ts', ''))}</td>"
            f"<td>{_esc(code)}</td>"
            f"<td>{_esc(ev.get('rule', ''))}</td>"
            f"<td>{_esc(ev.get('level', ''))}</td>"
            f"<td>{_esc(review)}</td>"
            f"<td>{_esc(ev.get('suppress_reason') or '')}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    return (
        '<table class="grid"><thead><tr>'
        "<th>ts</th><th>code</th><th>rule</th><th>level</th><th>复核短语</th><th>压制</th><th></th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_tasks_table(queue: dict) -> str:
    tasks = queue.get("tasks") or []
    if not tasks:
        return '<p class="muted">队列为空。</p>'
    rows = []
    for t in tasks[-20:]:
        rows.append(
            "<tr>"
            f"<td>{_esc(t.get('kind', ''))}</td>"
            f"<td>{_esc(t.get('code', ''))}</td>"
            f"<td>{_status_pill(t.get('status', ''))}</td>"
            f"<td>{_esc(t.get('enqueued_at', ''))}</td>"
            f"<td>{_esc(t.get('error') or '')}</td>"
            "</tr>"
        )
    return (
        '<table class="grid"><thead><tr>'
        "<th>kind</th><th>code</th><th>status</th><th>enqueued</th><th>error</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_task_card(task: dict) -> str:
    """任务卡：非终态带 5s 快轮询；终态停轮询并展示结果。"""
    status = str(task.get("status", "queued"))
    poll = (
        f'hx-get="/api/panel/task/{_esc(task.get("task_id", ""))}" '
        'hx-trigger="every 5s" hx-swap="outerHTML"'
        if status not in TERMINAL_STATUSES
        else ""
    )
    note = ""
    if status == "done":
        note = (
            '<p class="ok">任务完成（会话产物已过断言器落盘；报告见 vibe-stock reports 目录）。</p>'
        )
    elif status in ("failed", "abandoned"):
        note = f'<p class="warn">任务 {_esc(status)}：{_esc(task.get("error") or "")}</p>'
    return (
        f'<div class="task-card" id="task-card" {poll}>'
        f"<p><strong>{_esc(task.get('kind', ''))}</strong> · {_esc(task.get('code', ''))} · "
        f'<span class="st {_esc(status)}">{_esc(status)}</span></p>'
        f'<p class="muted">task_id {_esc(task.get("task_id", ""))}</p>'
        f"{note}</div>"
    )


def render_audit(decisions: list[dict], budget: dict) -> str:
    rows = []
    for line in decisions[-20:]:
        payload = line.get("payload") or {}
        summary = payload.get("kind") or payload.get("reason") or ""
        rows.append(
            "<tr>"
            f"<td>{int(line.get('seq', 0))}</td>"
            f"<td>{_esc(line.get('ts', ''))}</td>"
            f"<td>{_esc(line.get('kind', ''))}</td>"
            f"<td>{_esc(summary)}</td>"
            f"<td><code>{_esc(line.get('hash', '')[:12])}…</code></td>"
            "</tr>"
        )
    table = (
        '<table class="grid"><thead><tr><th>seq</th><th>ts</th><th>kind</th>'
        "<th>摘要</th><th>hash 链</th></tr>" + f"</thead><tbody>{''.join(rows)}</tbody></table>"
        if rows
        else '<p class="muted">decision log 为空。</p>'
    )
    return table + render_budget_line(budget)


def render_submit_result(task: dict | None, *, status: int, detail: str) -> str:
    if task is not None and status < 300:
        return f'<div id="submit-result">{render_task_card(task)}</div>'
    return (
        f'<div class="task-card" id="submit-result">'
        f'<p class="warn">提交被拒（{status}）：{_esc(detail)}</p></div>'
    )
