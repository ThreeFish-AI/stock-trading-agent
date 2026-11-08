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
    """任务卡：非终态带 5s 快轮询；终态停轮询并展示结果。

    0.0.13：有 session_uuid 时同步驱动②页「会话观测」区（同卡轮询双写，P2 案 a）。
    """
    status = str(task.get("status", "queued"))
    task_id = _esc(task.get("task_id", ""))
    session_uuid = str(task.get("session_uuid") or "")
    poll = (
        f'hx-get="/api/panel/task/{task_id}" hx-trigger="every 5s" hx-swap="outerHTML"'
        if status not in TERMINAL_STATUSES
        else ""
    )
    session_hook = ""
    if session_uuid:
        session_hook = (
            f' hx-get="/api/panel/session/{task_id}" hx-trigger="every 5s" '
            'hx-target="#dg-session" hx-swap="innerHTML"'
        )
    note = ""
    if status == "done":
        note = (
            '<p class="ok">任务完成（会话产物已过断言器落盘；报告见 vibe-stock reports 目录）。</p>'
        )
    elif status in ("failed", "abandoned"):
        note = f'<p class="warn">任务 {_esc(status)}：{_esc(task.get("error") or "")}</p>'
    return (
        f'<div class="task-card" id="task-card" {poll}{session_hook}>'
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


def render_settings_fragment(state: dict) -> str:
    """面板⑦设置区：L2 总闸（开启方向 hx-confirm 二次确认；关闭不确认）。"""
    enabled = bool(state.get("l2_enabled"))
    mode = state.get("billing_mode") or "未探测"
    if enabled:
        badge = '<span class="st done">L2 开启</span>'
    else:
        badge = '<span class="st queued">L2 关闭</span>'
    parts = [
        "<h3>L2 无人值守总闸（默认关；显式开启方生效）</h3>",
        f"<p>{badge} · billing_mode {_esc(mode)} · 更新 {_esc(state.get('updated_at', ''))}</p>",
        '<p class="muted">开启后：T2-worthy monitor 事件自动触发盯盘复核（每日 ≤5 票、'
        "预算闸联动开启、probe 认证态判定计费形态）。关闭仅封 monitor 源自动任务，"
        "面板手动提交不受影响。</p>",
    ]
    target = 'hx-target="#l2-settings" hx-swap="outerHTML"'
    if enabled:
        off_vals = 'hx-vals=\'{"enabled": "false"}\''
        parts.append(
            f'<button class="mini" hx-post="/api/panel/l2" {off_vals} {target}>关闭 L2</button>'
        )
    else:
        on_vals = 'hx-vals=\'{"enabled": "true"}\''
        confirm = (
            "hx-confirm='开启 L2 无人值守：monitor 事件将自动触发 T2 复核"
            "（每日 ≤5 票、预算闸联动开启）。确认？'"
        )
        parts.append(
            f'<button class="mini" hx-post="/api/panel/l2" {on_vals} {target} {confirm}>'
            "开启 L2</button>"
        )
    return "".join(parts)


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


# ═══ 0.0.11 §⑫ 新组件渲染器 ═══


def render_phase_chip(phase: dict) -> str:
    overall = str(phase.get("overall", "closed"))
    return (
        f'<span class="phase phase-{overall}" data-testid="phase">'
        f'<span class="pd pd-{overall}"></span>{phase.get("label", "")} · '
        f"{phase.get('weekday', '')}</span>"
    )


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value}{suffix}"


def render_diag_form() -> str:
    """②页发起诊股表单：六 kind 白名单下拉 + code 输入 + 提交。"""
    options = "".join(
        f'<option value="{k}">{label}</option>'
        for k, label in [
            ("diagnose_quick", "快速诊股"),
            ("diagnose_full", "全量诊股（双轨）"),
            ("batch_wave", "批量波次"),
            ("paper_confirm", "模拟盘逐笔确认（CC 辅助）"),
            ("briefing_review", "盘前晨报/盘后复盘"),
            ("monitor_review", "盯盘复核（T2）"),
        ]
    )
    return (
        '<form class="inrow" hx-post="/api/panel/submit" hx-target="#dg-result" '
        'hx-swap="innerHTML" style="display:flex;flex-direction:column;gap:10px">'
        '<div style="display:flex;gap:8px">'
        '<input class="inp" name="code" placeholder="600519 / 00700" '
        'pattern="[0-9]{5,6}" required style="flex:1">'
        f'<select class="sel" name="kind" style="flex:1.4">{options}</select>'
        '<button class="btn btn-pri" type="submit">开始</button></div>'
        '<p class="tbl-note">LLM 只产意见 · 聚合一律确定性裁决 · NR-3 断言器前置门 · '
        "锁/禁跑窗内提交将被拒（423）</p></form>"
        '<div id="dg-result" style="margin-top:10px"></div>'
    )


def render_event_feed(payload: dict) -> str:
    """①今日事件流：时间轴形态（T 徽章/mech_risk riskchip/风险行高亮/现价与距线）。"""
    events = (payload.get("events") or [])[-10:]
    if not events:
        return '<p class="loadmsg">暂无盯盘事件（watch.py 台账为空或未运行）。</p>'
    rows = []
    for ev in events:
        facts = ev.get("facts") or {}
        price = facts.get("price") or facts.get("pct")
        mech = ev.get("mech_risk") or {}
        risk_cls = " breach" if mech else ""
        riskchip = (
            f'<span class="riskchip">mech_risk {mech.get("severity", "")}</span>' if mech else ""
        )
        price_txt = f"现价 {price}" if price is not None else ""
        review_txt = f" · {ev['review']}" if ev.get("review") else ""
        rows.append(
            f'<div class="ev{risk_cls}"><div class="ev-time num">{_esc(ev.get("ts", ""))}</div>'
            f'<div class="ev-rail"><div class="ev-dot"></div></div>'
            f'<div class="ev-name"><span class="ev-stock"><span class="code num">'
            f"{_esc(ev.get('code', ''))}</span> {_esc(ev.get('rule', ''))}</span>"
            f'<span class="ev-top">{_esc(price_txt)} {riskchip}'
            f'<span class="lv lv0">{_esc(ev.get("level", "T0"))}</span>'
            f"{_esc(review_txt)}</span></div></div>"
        )
    return "".join(rows)


def render_calendar(payload: dict) -> str:
    """①事件日历：日期升序 kchip 条目（披露/解禁/公告）。"""
    rows = payload.get("upcoming") or []
    if not rows:
        return (
            '<p class="loadmsg">暂无日历事件（需 T1 刷新或诊股会话产出 evidence；'
            "A 股专属块对港股不适用）。</p>"
        )
    items = []
    for row in rows:
        items.append(
            f'<div class="kchip"><span class="code num">{_esc(row["code"])}</span>'
            f'<span class="num">{_esc(row["date"])}</span>'
            f'<span class="tag">{_esc(row["type"])}</span>'
            f"<span>{_esc(row['detail'])}</span></div>"
        )
    return "".join(items)


def render_notifications(payload: dict) -> str:
    """③通知预览：已发通知历史重放（macOS 通知样式卡 + 红线 7 说明）。"""
    rows = payload.get("items") or []
    if not rows:
        cards = '<p class="loadmsg">暂无已发通知（watch.py osascript 历史；产品仓永不发通知）。</p>'
    else:
        cards = "".join(
            f'<div class="card" style="margin-bottom:8px"><div class="card-b" '
            'style="display:flex;gap:10px;align-items:flex-start">'
            '<svg width="16" height="16" viewBox="0 0 16 16" style="flex:none;margin-top:2px">'
            '<path d="M8 1a4 4 0 0 0-4 4v3l-1.5 2.5h11L12 8V5a4 4 0 0 0-4-4z'
            'M6.5 12.5a1.5 1.5 0 0 0 3 0" '
            'stroke="#8792A6" stroke-width="1.3" fill="none"/></svg>'
            f'<div style="flex:1;min-width:0">'
            f'<div class="num" style="font-size:11px;color:var(--t4)">'
            f"{_esc(row.get('ts', ''))} · 现在</div>"
            f'<div style="font-size:12.5px;font-weight:600">'
            f"{_esc(row.get('title', ''))}</div>"
            f'<div style="font-size:12px;color:var(--t2)">'
            f"{_esc(row.get('body', ''))}</div></div></div></div>"
            for row in rows
        )
    return (
        f"{cards}"
        '<p class="tbl-note">仅本地 osascript（terminal 通道）· 禁第三方推送（红线 7）· '
        "告警主通道永久留服务端（后台标签页冻结，I-48）——本卡为只读历史重放</p>"
    )


def render_session_obs(body: dict) -> str:
    """②会话观测：五段进度条（done/cur/pend）+ 双轨意见板 + 裁决卡（P2 §⑫ 案 a）。"""
    stages = body.get("stages") or []
    if not stages:
        return '<p class="loadmsg">无会话产物视图。</p>'
    steps = []
    for st in stages:
        cls = {"done": "done", "current": "cur", "pending": "pend"}.get(st.get("state"), "pend")
        steps.append(
            f'<div class="step {cls}"><div class="step-dot"></div>'
            f'<div class="step-tx">{st["stage"]}·{_esc(st["name"])}</div>'
            f'<div class="step-sub num">{_esc(st.get("note", ""))}</div></div>'
        )
    parts = ['<div class="pipe" style="display:flex;gap:4px;flex-wrap:wrap">']
    parts.append("".join(steps))
    parts.append("</div>")
    opinions = body.get("opinions") or {}
    default_cards = opinions.get("default") or []
    sig_map = {
        "strong_buy": "sig-sb",
        "buy": "sig-b",
        "hold": "sig-h",
        "sell": "sig-s",
        "strong_sell": "sig-ss",
    }
    if default_cards:
        cards = "".join(
            f'<div class="op"><div class="op-src num">{_esc(c["source"])}</div>'
            f'<div><span class="{sig_map.get(c.get("signal"), "")}"></span> '
            f'<span class="num">{_esc(c.get("confidence"))}</span></div></div>'
            for c in default_cards[:16]
        )
        position_n = len(opinions.get("position") or [])
        pos_note = f"（中线轨 {position_n} 条另裁决）" if position_n else ""
        parts.append(
            '<h3 style="margin:10px 0 6px;font-size:13px">'
            f"双轨意见板 · {len(default_cards)} 条{pos_note}</h3>"
        )
        parts.append(
            '<div class="opins" style="display:grid;grid-template-columns:'
            f'repeat(auto-fill,minmax(180px,1fr));gap:8px">{cards}</div>'
        )
    else:
        parts.append('<p class="loadmsg">意见未产出（LLM 意见段待会话推进）。</p>')
    verdict = body.get("verdict")
    if verdict:
        buckets = verdict.get("buckets") or {}
        bucket_line = " · ".join(f"{_esc(k)} {len(v)}" for k, v in buckets.items())
        rg_log = "；".join(verdict.get("risk_guard_log") or []) or "no-op"
        parts.append('<div class="verd" style="margin-top:10px;border:1px solid var(--line);')
        parts.append('border-radius:8px;padding:10px">')
        parts.append(
            f'<div class="num" style="font-size:12px;color:var(--t4)">'
            f"initial {_esc(verdict.get('initial'))} → final "
            f'<b style="font-size:16px;color:var(--t1)">{_esc(verdict.get("final"))}</b>'
            f" · 置信 {_esc(verdict.get('confidence'))} · "
            f"{_esc(verdict.get('consensus_level'))}</div>"
        )
        parts.append(
            f'<div style="font-size:11.5px;color:var(--t3);margin-top:4px">'
            f"RiskGuard：{_esc(rg_log)}</div>"
        )
        parts.append(
            f'<div style="font-size:11.5px;color:var(--t3)">分歧：{_esc(bucket_line) or "无"}</div>'
        )
        parts.append("</div>")
        parts.append(
            '<p class="tbl-note">LLM 零直写裁决面（R3）· RiskGuard 只降不升（R5）'
            "· 裁决链经 adjudicate 原命令</p>"
        )
    notes = body.get("notes") or []
    if notes:
        parts.append(f'<p class="loadmsg">{_esc(notes[0])}</p>')
    return "".join(parts)


def render_signals_table(payload: dict) -> str:
    """①在册信号卡表 6 列（信号五词/置信/距止损/轨）。"""
    rows = payload.get("rows") or []
    if not rows:
        note = "" if payload.get("read_ok") else "（signals.json 不可读——宁拒不漏）"
        return f'<p class="loadmsg">无在册信号{note}。</p>'
    names = payload.get("names") or {}
    trs = []
    for row in rows:
        signal = row.get("signal")
        sig_cls = {
            "strong_buy": "sig-sb",
            "buy": "sig-b",
            "hold": "sig-h",
            "sell": "sig-s",
            "strong_sell": "sig-ss",
        }.get(signal, "")
        to_stop = row.get("to_stop_pct")
        dist_cls = (
            "dist-breach"
            if to_stop is not None and to_stop <= 0
            else ("dist-near" if to_stop is not None and to_stop <= 3 else "")
        )
        name = names.get(row["code"])
        name_html = f" {_esc(name)}" if name else ""
        trs.append(
            "<tr>"
            f'<td><span class="code num">{_esc(row["code"])}</span>{name_html}</td>'
            f'<td><span class="{sig_cls}"></span> {_esc(signal or row.get("signal_raw"))}</td>'
            f'<td class="num">{_fmt(row.get("confidence"))}</td>'
            f'<td class="num {_esc(dist_cls)}">{_fmt(to_stop, "%")}</td>'
            f"<td>{_esc('中线' if row.get('profile') == 'position' else '短线')}</td>"
            f'<td class="num">{_esc(row.get("date", ""))}</td>'
            "</tr>"
        )
    return (
        '<table class="tbl"><thead><tr><th>代码</th><th>最新信号</th><th>置信</th>'
        f"<th>距止损</th><th>轨</th><th>信号日</th></tr></thead><tbody>{''.join(trs)}</tbody></table>"
        '<p class="tbl-note">adjudicate 产出 · 盘中结论以收盘复核为准（R9）</p>'
    )


def render_todos(payload: dict) -> str:
    """①今日待办卡：三源聚合（pending 优先）。"""
    todos = payload.get("todos") or []
    if not todos:
        return '<p class="loadmsg">无待办。</p>'
    rows = []
    for item in todos:
        rows.append(
            f'<div class="todo"><div class="cbx"></div><div style="flex:1;min-width:0">'
            f'<span class="code num">{_esc(item["code"])}</span> {_esc(item["text"])}</div>'
            f'<span class="chip chip-warn">{_esc(item["chip"])}</span></div>'
        )
    return "".join(rows)


def render_navcard(view: dict) -> str:
    """①模拟盘净值卡：NAV 大数字 + 今日%/成立以来% + sparkline。"""
    history = view.get("equity_history") or []
    if not history:
        return '<p class="loadmsg">权益曲线待积累（盘后日终复核产出）。</p>'
    navs = [float(p.get("nav") or 0) for p in history]
    nav = navs[-1]
    today_pct = ((nav / navs[-2] - 1) * 100) if len(navs) >= 2 and navs[-2] else None
    since_pct = (nav - 1.0) * 100
    up_today = (today_pct or 0) >= 0
    chip = f'<span class="chip {"chip-up" if up_today else "chip-neutral"}">'
    return (
        f'<div class="nv-v num">{nav:.4f}</div>'
        '<div style="display:flex;gap:8px;margin:6px 0 8px">'
        f"{chip}今日 {today_pct:+.2f}%</span>"
        if today_pct is not None
        else ""
    ) + (
        f'<span class="chip chip-neutral">成立以来 {since_pct:+.2f}%</span></div>'
        + _sparkline_svg([{"nav": n} for n in navs])
        + '<p class="tbl-note">初始 1.0000 · 含费用与滑点（假设性推演）</p>'
    )


def render_syscard(status: dict, sessions: dict, queue: dict) -> str:
    """①系统状态卡：三槽调度时刻+分级+CC 行+在跑会话。"""
    slots = status.get("slots") or {}
    sched = {"monitor": "09:15", "outlook": "17:35", "postmortem": "18:05"}
    rows = []
    for name in ("monitor", "outlook", "postmortem"):
        done = bool((slots.get(name) or {}).get("done"))
        dot = "sdot-ok" if done else "sdot-wait"
        state = "已收口" if done else "待触发"
        desc = {
            "monitor": "盯盘轮询与收盘复核",
            "outlook": "走势参考日表",
            "postmortem": "信号后验记分",
        }[name]
        rows.append(
            f'<div class="sys-row"><div class="sdot {dot}"></div>'
            f'<div style="flex:1;min-width:0"><span class="sys-name">{name}</span>'
            f'<div class="sys-desc">{desc}</div></div>'
            f'<span class="sys-sched num">{sched[name]}</span>'
            f'<span class="sys-st">{state}</span></div>'
        )
    cc_active = bool((status.get("loop") or {}).get("active"))
    rows.append(
        f'<div class="sys-row"><div class="sdot {"sdot-ok" if cc_active else "sdot-wait"}"></div>'
        '<div style="flex:1;min-width:0"><span class="sys-name">CC 大脑</span>'
        '<div class="sys-desc">headless 子进程唯一 LLM 通道</div></div>'
        f'<span class="sys-st">{"运行中" if cc_active else "降级只读"}</span></div>'
    )
    chain_error = (status.get("loop") or {}).get("chain_error")
    if chain_error:
        rows.append(
            '<div class="sys-row"><div class="sdot sdot-wait"></div>'
            '<div style="flex:1;min-width:0"><span class="sys-name">决策链</span>'
            f'<div class="sys-desc">{html.escape(str(chain_error))}</div></div>'
            '<span class="sys-st">待修复</span></div>'
        )
    sess_count = len(sessions.get("sessions") or {})
    latest = list((sessions.get("sessions") or {}).values())[-1:] or [{}]
    cost = latest[0].get("cost_usd_equiv", 0) if isinstance(latest[0], dict) else 0
    queued = (status.get("loop") or {}).get("queued", 0)
    running = (status.get("loop") or {}).get("running", 0)
    body = "".join(rows)
    return (
        f"{body}"
        f'<p class="tbl-note">在册会话 {sess_count} · 队列 queued {queued} / running {running}'
        f" · 最新会话等效 {cost} USD</p>"
    )


def render_reports_list(payload: dict) -> str:
    """②报告档案列表（目录+latest+count，点击查正文）。"""
    rows = payload.get("reports") or []
    if not rows:
        return '<p class="loadmsg">暂无诊股报告（提交诊股后由 vibe-stock report 段产出）。</p>'
    trs = []
    for row in rows[:20]:
        link = (
            f'<a href="#" hx-get="/api/panel/report/{_esc(row["dir"])}/{_esc(row["latest"])}" '
            'hx-target="#dg-report-body" hx-swap="innerHTML">'
            f'<span class="code num">{_esc(row["code"])}</span>.{_esc(row["market"])}</a>'
        )
        trs.append(
            f'<tr><td>{link}</td><td class="num">{_esc(row.get("latest", ""))}</td>'
            f'<td class="num">{row.get("count", 0)}</td></tr>'
        )
    return (
        '<table class="tbl"><thead><tr><th>标的</th><th>最新报告</th><th>在档</th>'
        f"</tr></thead><tbody>{''.join(trs)}</tbody></table>"
        '<p class="tbl-note">04 规约结构 · 免责段必含</p>'
    )


def render_report_body(code_market: str, filename: str, md: str) -> str:
    has_disclaimer = "不构成投资建议" in md
    warn = "" if has_disclaimer else '<p class="riskchip">免责段缺失（异常）</p>'
    return (
        f'<div class="card"><div class="card-h"><span class="ct">报告 · {_esc(code_market)} · '
        f'{_esc(filename)}</span><span class="cm">md 直读</span></div>'
        f'<div class="card-b">{warn}<pre style="white-space:pre-wrap;font-size:12.5px;'
        f'line-height:1.75;color:var(--t2)">{_esc(md)}</pre></div></div>'
    )


def render_advice_detail(body: dict) -> str:
    """③⑤建议卡详情：四块布局（候选动作/差异/份额原值 .bignum/σ√h 表）。"""
    meta = body.get("meta") or {}
    rendered = body.get("rendered")
    from stock_trading_agent.loop.advice import parse_trend_table

    band_rows = parse_trend_table(body.get("md", ""))
    prev = meta.get("prev_final_signal", "none")
    final = meta.get("final_signal", "")
    shares = (
        f'<div class="bignum num">{rendered["t1_sell_shares"]:,}</div>'
        f'<p class="tbl-note">持仓 {rendered["held_lots"]} 手 · 冻结后可卖 '
        f"{rendered['t1_sell_lots']} 手 × {rendered['board_lot']}/手（T+1，asof "
        f"{_esc(rendered['asof'])}）——呈现态原值（NR-5/R2 豁免面，落盘为百分比）</p>"
        if rendered
        else '<p class="loadmsg">无模拟持仓（原值不可现算；落盘口径见卡面百分比）。</p>'
    )
    trend_rows = "".join(
        f'<tr><td>{_esc(r["horizon"])}</td><td class="num">{r["low"]}</td>'
        f'<td class="num">{r["high"]}</td><td class="num">{r["prob_touch_low_pct"]}%</td></tr>'
        for r in band_rows
    )
    trend = (
        '<table class="tbl"><thead><tr><th>h</th><th>带下沿</th><th>带上沿</th>'
        f"<th>P 触下</th></tr></thead><tbody>{trend_rows}</tbody></table>"
        if band_rows
        else '<p class="loadmsg">无走势参考表。</p>'
    )
    return (
        f'<div class="card adv"><div class="card-h">'
        f'<span class="lv lv2">monitor-advice/1</span>'
        '<span class="ct">T2 建议卡 · '
        f'<span class="code num">{_esc(meta.get("code", ""))}</span></span>'
        '<span class="cm">已重裁决 ✓ NR-1 · 待用户复核</span></div>'
        '<div class="card-b"><div class="adv-grid">'
        f'<div class="adv-blk"><div class="adv-k">候选动作（04 五档措辞）</div>'
        f'<div class="quote">「{_esc(meta.get("candidate_action", ""))}」</div></div>'
        f'<div class="adv-blk"><div class="adv-k">与上一裁决差异（adjudicate 重裁决产出）</div>'
        f'<div class="num">{_esc(prev)} → {_esc(final)}</div></div>'
        f'<div class="adv-blk"><div class="adv-k">T+1 可卖份额（呈现态）</div>{shares}</div>'
        f'<div class="adv-blk"><div class="adv-k">走势参考（σ√h · 零权重）</div>{trend}</div>'
        "</div>"
        '<p class="tbl-note">盘中结论以收盘复核为准 · 候选动作为五档分类学措辞 · 不构成投资建议</p>'
        "</div></div>"
    )


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
