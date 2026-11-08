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


def _tranche_badge(t: dict) -> str:
    """建仓批徽章：T1/T2/T3 前缀 + 状态着色（filled=done、pending=queued、expired=warn）。"""
    label = _esc(str(t.get("leg", "")).split("_")[0])
    status = str(t.get("status", "pending"))
    cls = {"filled": "done", "pending": "queued", "expired": "warn"}.get(status, "queued")
    return f'<span class="st {cls}">{label}</span>'


def _sparkline_svg(points: list[dict], *, width: int = 220, height: int = 44) -> str:
    """权益 sparkline（服务端手搓内联 SVG，零 CDN/零前端库）。"""
    navs = [float(p.get("nav") or 0) for p in points]
    if len(navs) < 2:
        return '<p class="muted">权益曲线待积累（≥2 个交易日点）。</p>'
    lo, hi = min(navs), max(navs)
    span = (hi - lo) or 1e-9
    step = width / (len(navs) - 1)
    coords = [
        f"{i * step:.1f},{height - (v - lo) / span * (height - 6) - 3:.1f}"
        for i, v in enumerate(navs)
    ]
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
        f'<polyline fill="none" stroke="#58a6ff" stroke-width="1.5" points="{" ".join(coords)}"/>'
        f"</svg>"
    )


def render_stats_fragment(payload: dict) -> str:
    """面板⑥：kind 分布 + 任务成功率（样本不足标注）+ 预算双口径小表（§15）。"""
    d = payload.get("decisions") or {}
    rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{v}</td></tr>" for k, v in (d.get("kinds") or {}).items()
    )
    rate = d.get("success_rate")
    rate_text = f"{rate:.0%}" if rate is not None else "—"
    summary = (
        f"done {d.get('done', 0)} · failed {d.get('failed', 0)} · "
        f"abandoned {d.get('abandoned', 0)} · 成功率 {rate_text}"
    )
    if d.get("note"):
        summary += f"（{_esc(d.get('note'))}）"
    parts = [
        "<h3>任务画像（全样本登记，含失败与弃单）</h3>",
        f'<p class="muted">{summary}</p>',
        '<table class="grid"><thead><tr><th>决策 kind</th><th>计数</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>",
        "<h3>预算曲线（token + 等效美元双口径，F-34）</h3>",
    ]
    curve = payload.get("budget_curve") or []
    if curve:
        crows = "".join(
            "<tr>"
            f"<td>{_esc(p.get('date'))}</td><td>{p.get('sessions', 0)}</td>"
            f"<td>{p.get('tokens', 0)}</td><td>{p.get('cost_usd_equiv', 0)}</td>"
            "</tr>"
            for p in curve
        )
        parts.append(
            '<table class="grid"><thead><tr><th>日</th><th>会话</th><th>token</th>'
            f"<th>等效 USD</th></tr></thead><tbody>{crows}</tbody></table>"
        )
    else:
        parts.append('<p class="muted">暂无会话记账。</p>')
    parts.append('<p class="muted">后验画像：全样本登记含亏损；模拟绩效均为假设性推演。</p>')
    return "".join(parts)


def render_briefing_fragment(payload: dict) -> str:
    """晨报/复盘卡区：当日卡列表 + ack 状态 + 触发按钮（复用 submit；禁跑窗提示）。"""
    day = payload.get("date", "")
    cards = payload.get("cards") or []
    parts = [f"<h3>晨报 / 复盘行动项卡 · {day}</h3>"]
    if cards:
        for card in cards:
            ack = bool(card.get("ack"))
            code = _esc(card.get("code", ""))
            if ack:
                state = '<span class="st done">已确认</span>'
                action = ""
            else:
                state = '<span class="st queued">待确认</span>'
                vals = f'{{"date": "{_esc(day)}", "code": "{code}"}}'
                action = (
                    '<button class="mini" hx-post="/api/panel/briefing/ack" '
                    f"hx-vals='{vals}' "
                    'hx-target="#briefing" hx-swap="outerHTML">确认复核</button>'
                )
            body = _esc(str(card.get("md", ""))[:1200])
            parts.append(
                f'<div class="task-card"><p><strong>{code}</strong> · {state} {action}</p>'
                f'<pre style="white-space:pre-wrap;font-size:12px;color:#8b949e">'
                f"{body}</pre></div>"
            )
    else:
        parts.append(
            '<p class="muted">当日无卡（面板触发 briefing_review 生成；'
            "盘后复盘建议 18:10 后触发）。</p>"
        )
    return "".join(parts)


def render_paper_fragment(payload: dict, *, notice: str = "") -> str:
    """面板④：NR-2 常驻条 + 持仓表（T1/T2/T3 徽章 + 可卖(T+1) + 市值/盈亏%）+ 确认队列 + 权益曲线。

    NR-5 呈现态豁免（13-paper §⑥）：面板可显份额原值（导出/落盘卡仍降级脱敏）。
    """
    parts = []
    if notice:
        parts.append(f'<p class="ok">{_esc(notice)}</p>')
    parts.append(
        '<p class="warn"><strong>NR-2</strong>：模拟盘永不接真实下单接口；'
        "全部模拟绩效为假设性推演（含费用与滑点）。</p>"
    )
    positions = payload.get("positions") or []
    if positions:
        rows = []
        for pos in positions:
            badges = " ".join(_tranche_badge(t) for t in pos.get("tranches", []))
            last = pos.get("last_price") or 0.0
            cost = pos.get("avg_cost") or 0.0
            pnl_pct = round((last - cost) / cost * 100, 2) if cost and last else None
            rows.append(
                "<tr>"
                f"<td>{_esc(pos['code'])}</td>"
                f"<td>{_status_pill(pos['status'])}</td>"
                f"<td>{badges}</td>"
                f"<td>{pos.get('lots', 0)} / 可卖 {pos.get('sellable_lots', 0)}（T+1）</td>"
                f"<td>{_esc(pos.get('avg_cost'))}</td>"
                f"<td>{_esc(pos.get('last_price'))}</td>"
                f"<td>{pnl_pct if pnl_pct is not None else '—'}%</td>"
                f"<td>{_esc(round(pos.get('market_value') or 0, 2))}</td>"
                "</tr>"
            )
        parts.append(
            '<table class="grid"><thead><tr><th>code</th><th>状态</th><th>建仓批</th>'
            "<th>手数/可卖</th><th>成本价</th><th>现价</th><th>盈亏%</th><th>市值</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        parts.append('<p class="muted">无模拟持仓（账本未建或全部清仓）。</p>')

    pending = payload.get("pending") or []
    parts.append("<h3>逐笔确认队列（触价只写待确认；无人工确认不落账）</h3>")
    if pending:
        rows = []
        for req in pending:
            if req.get("state") != "pending":
                continue
            rid = _esc(req.get("request_id", ""))
            confirm_btn = (
                f'<button class="mini" hx-post="/api/panel/paper/confirm" '
                f'hx-vals=\'{{"request_id": "{rid}"}}\' hx-target="#paper" '
                'hx-swap="outerHTML">确认成交</button>'
            )
            reject_btn = (
                f'<button class="mini" hx-post="/api/panel/paper/reject" '
                f'hx-vals=\'{{"request_id": "{rid}"}}\' hx-target="#paper" '
                'hx-swap="outerHTML">驳回</button>'
            )
            rows.append(
                "<tr>"
                f"<td>{_esc(req.get('ts', ''))}</td>"
                f"<td>{_esc(req.get('code', ''))}</td>"
                f"<td>{_esc(req.get('tranche_leg', ''))}</td>"
                f"<td>{_esc(req.get('side', ''))}</td>"
                f"<td>{_esc(req.get('lots'))}</td>"
                f"<td>{_esc(req.get('ref_price'))}</td>"
                f"<td>{_esc(req.get('bar_date', ''))}</td>"
                f"<td>{confirm_btn} {reject_btn}</td>"
                "</tr>"
            )
        parts.append(
            '<table class="grid"><thead><tr><th>ts</th><th>code</th><th>批</th><th>方向</th>'
            "<th>手数</th><th>参考价</th><th>bar 日</th><th></th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        parts.append('<p class="muted">队列为空。</p>')

    parts.append("<h3>权益曲线（NAV = 权益 / 预算）</h3>")
    history = payload.get("equity_history") or []
    if history:
        latest = history[-1]
        parts.append(_sparkline_svg(history))
        parts.append(
            f'<p class="muted">最新 {latest["date"]} · NAV {latest["nav"]} · '
            f"现金 {latest['cash']} · 市值 {latest['market_value']}（假设性 · 含费用与滑点）</p>"
        )
    else:
        parts.append('<p class="muted">权益曲线待积累。</p>')
    return "".join(parts)


def render_advice_library(payload: dict) -> str:
    """面板⑤：建议卡库（当日卡列表）+ 拒卡/降级通报区（脱敏字段直接透出）。"""
    cards = payload.get("cards") or []
    refused = payload.get("refused") or []
    parts = [f"<h3>建议卡 · {_esc(payload.get('date', ''))}</h3>"]
    if cards:
        rows = []
        for card in cards:
            rows.append(
                "<tr>"
                f"<td>{_esc(card.get('code', ''))}</td>"
                f"<td>{_esc(card.get('candidate_action', ''))}</td>"
                f"<td>{_esc(card.get('final_signal', ''))}</td>"
                f"<td>{_esc(card.get('prev_final_signal', ''))}</td>"
                f"<td>{_esc(card.get('t1_sell_pct', ''))}% · {_esc(card.get('t1_sell_tier'))}</td>"
                f"<td>{_esc(card.get('mtime', ''))}</td>"
                "</tr>"
            )
        parts.append(
            '<table class="grid"><thead><tr><th>code</th><th>候选动作</th><th>终判</th>'
            "<th>上一裁决</th><th>T+1 可卖</th><th>落盘</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        parts.append('<p class="muted">当日无建议卡（T2 复核未触发或被降级）。</p>')
    parts.append("<h3>拒卡 / 降级通报</h3>")
    if refused:
        rows = []
        for item in refused:
            rows.append(
                "<tr>"
                f"<td>{_esc(item.get('ts', ''))}</td>"
                f"<td>{_esc(item.get('code', ''))}</td>"
                f"<td>{_esc(item.get('origin', ''))}</td>"
                f"<td>{_esc(item.get('reason_code', ''))}</td>"
                f"<td>{_esc((item.get('facts_note') or '')[:80])}</td>"
                "</tr>"
            )
        parts.append(
            '<table class="grid"><thead><tr><th>ts</th><th>code</th><th>origin</th>'
            "<th>reason</th><th>通报</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
    else:
        parts.append('<p class="muted">无拒卡/降级记录。</p>')
    parts.append(
        '<p class="muted">卡面脱敏口径：只落百分比与档位词（份额原值禁入落盘产物，R2/NR-5）；'
        "候选动作为 vibe-stock/04 五档分类学措辞。</p>"
    )
    return "".join(parts)


def render_submit_result(task: dict | None, *, status: int, detail: str) -> str:
    if task is not None and status < 300:
        return f'<div id="submit-result">{render_task_card(task)}</div>'
    return (
        f'<div class="task-card" id="submit-result">'
        f'<p class="warn">提交被拒（{status}）：{_esc(detail)}</p></div>'
    )
