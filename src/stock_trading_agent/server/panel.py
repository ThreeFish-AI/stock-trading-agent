"""面板 HTML fragment 渲染（服务端手搓，html.escape 全转义防注入；零模板依赖，§16）。

htmx 消费这些 fragment：常态 180s 轮询、任务卡非终态 5s 快轮询（终态停）。
NR-5：只透出脱敏态数据（monitor events/universe 本身脱敏；sessions/budget 为运营元数据）。
"""

from __future__ import annotations

import html
import json

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
    """③盯盘事件表（原型七列 + mech_risk）：现价/距线来自 events facts，名称来自注入。

    advice_codes（当日建议卡 stem 集）在场的行给「详情」按钮，填充③页 #advice-detail；
    距线 = price/level − 1，≤0 标 breach、≤3% 标 near（与①信号卡同判据）。
    """
    events = (payload.get("events") or [])[-tail:]
    if not events:
        return '<p class="muted">暂无盯盘事件（watch.py 台账为空或未运行）。</p>'
    names = payload.get("names") or {}
    advice_codes = set(payload.get("advice_codes") or [])
    day = payload.get("date") or ""
    rows = []
    for ev in events:
        code = ev.get("code", "*")
        review = ev.get("review") or ""
        suppress = ev.get("suppress_reason") or ""
        suppressed = bool(suppress)
        facts = ev.get("facts") or {}
        price = facts.get("price")
        level = facts.get("level")
        dist_txt = dist_cls = ""
        if isinstance(price, (int, float)) and isinstance(level, (int, float)) and level:
            dist = (price / level - 1) * 100
            dist_txt = f"{dist:+.2f}%"
            dist_cls = "dist-breach" if dist <= 0 else ("dist-near" if dist <= 3 else "")
        mech = ev.get("mech_risk") or {}
        riskchip = (
            f'<span class="riskchip">mech_risk {_esc(mech.get("severity", ""))}</span>'
            if mech
            else ""
        )
        name = names.get(code)
        name_html = f" {_esc(name)}" if name else ""
        action = ""
        if code in advice_codes and day:
            action += (
                f'<button class="mini" hx-get="/api/panel/advice-detail/{_esc(day)}/{_esc(code)}" '
                'hx-target="#advice-detail" hx-swap="innerHTML">详情</button> '
            )
        if review and not suppressed:
            action += (
                f'<button class="mini" hx-post="/api/panel/submit" '
                f'hx-vals=\'{{"kind": "monitor_review", "code": "{_esc(code)}"}}\' '
                f'hx-target="#advice-detail" hx-swap="innerHTML" '
                f'hx-disabled-elt="this">盯盘复核 {_esc(code)}</button>'
            )
        elif suppressed:
            action = '<span class="tag">已压制</span>'
        rows.append(
            "<tr>"
            f'<td class="num" title="{_esc(ev.get("ts", ""))}">{_esc(ev.get("ts", ""))}</td>'
            f'<td><span class="code num">{_esc(code)}</span>{name_html}</td>'
            f'<td title="{_esc(ev.get("rule", ""))}">{_esc(ev.get("rule", ""))}</td>'
            f'<td class="num">{_fmt(price)}</td>'
            f'<td class="num {_esc(dist_cls)}">{dist_txt or "—"}</td>'
            f'<td><span class="lv lv0">{_esc(ev.get("level", "T0"))}</span></td>'
            f"<td>{riskchip or '—'}</td>"
            f'<td title="{_esc(review)}">{_esc(review or "—")}</td>'
            f'<td title="{_esc(suppress)}">{_esc(suppress or "—")}</td>'
            f"<td>{action}</td>"
            "</tr>"
        )
    return (
        '<table class="tbl"><colgroup><col class="w-ts"><col class="w-code">'
        '<col><col class="w-num"><col class="w-num"><col><col><col><col class="w-sup">'
        '<col class="w-op"></colgroup><thead><tr>'
        '<th scope="col">时间</th><th scope="col">股票</th><th scope="col">事件</th>'
        '<th scope="col">现价</th><th scope="col">距线</th><th scope="col">级别</th>'
        '<th scope="col">机械风险</th><th scope="col">复核短语</th><th scope="col">压制</th>'
        '<th scope="col">操作</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        '<p class="tbl-note">盯盘轮询零 LLM · 事件语义对齐 vibe-stock/07 · '
        "盘中结论以收盘复核为准（R9）</p>"
    )


def render_tasks_table(queue: dict) -> str:
    tasks = queue.get("tasks") or []
    if not tasks:
        return (
            '<p class="muted">队列为空。</p><span id="dg-task-meta" hx-swap-oob="true">0 项</span>'
        )
    rows = []
    for t in tasks[-20:]:
        err = t.get("error") or ""
        rows.append(
            "<tr>"
            f"<td>{_esc(t.get('kind', ''))}</td>"
            f'<td><span class="code num">{_esc(t.get("code", ""))}</span></td>'
            f"<td>{_status_pill(t.get('status', ''))}</td>"
            f'<td class="num" title="{_esc(t.get("enqueued_at", ""))}">'
            f"{_esc(t.get('enqueued_at', ''))}</td>"
            f'<td title="{_esc(err)}">{_esc(err or "—")}</td>'
            "</tr>"
        )
    return (
        '<table class="tbl"><colgroup><col class="w-kind"><col class="w-code">'
        '<col class="w-st"><col class="w-ts"><col></colgroup><thead><tr>'
        '<th scope="col">类型</th><th scope="col">代码</th><th scope="col">状态</th>'
        '<th scope="col">入队</th><th scope="col">错误</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f'<span id="dg-task-meta" hx-swap-oob="true">{len(tasks)} 项</span>'
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


def render_audit(decisions: list[dict], budget: dict, *, chain_ok: bool | None = None) -> str:
    rows = []
    for line in decisions[-20:]:
        payload = line.get("payload") or {}
        summary = payload.get("kind") or payload.get("reason") or ""
        rows.append(
            "<tr>"
            f'<td class="num">{int(line.get("seq", 0))}</td>'
            f'<td class="num" title="{_esc(line.get("ts", ""))}">{_esc(line.get("ts", ""))}</td>'
            f"<td>{_esc(line.get('kind', ''))}</td>"
            f'<td title="{_esc(summary)}">{_esc(summary or "—")}</td>'
            f'<td class="num">{_esc(line.get("hash", "")[:12])}…</td>'
            "</tr>"
        )
    chain_note = ""
    if chain_ok is True:
        chain_note = '<p class="ok">链校验通过 · 台账防篡改完好</p>'
    elif chain_ok is False:
        from stock_trading_agent.state.decisions import REPAIR_HINT

        chain_note = (
            '<p class="warn">hash 链校验失败（seq 乱序或 hash 复算不符）——'
            f"台账可能被改写或并发写入，{_esc(REPAIR_HINT)}</p>"
        )
    table = (
        '<table class="tbl"><colgroup><col class="w-seq"><col class="w-ts">'
        '<col class="w-kind"><col><col class="w-hash"></colgroup>'
        '<thead><tr><th scope="col">序号</th><th scope="col">时间</th>'
        '<th scope="col">类型</th><th scope="col">摘要</th><th scope="col">hash 链</th></tr>'
        + f"</thead><tbody>{''.join(rows)}</tbody></table>"
        if rows
        else '<p class="muted">decision log 为空。</p>'
    )
    body = table + chain_note + render_budget_line(budget)
    return (
        '<div class="card"><div class="card-h"><span class="ct">decisions 台账（尾部 20 条）</span>'
        '<span class="cm">hash 链防篡改 · 只读投影</span></div>'
        f'<div class="card-b">{body}</div></div>'
    )


def _tranche_badge(t: dict) -> str:
    """建仓批徽章（原型 .leg 形态）：T1/T2/T3 前缀 + filled/pending/expired 着色。"""
    label = _esc(str(t.get("leg", "")).split("_")[0])
    status = str(t.get("status", "pending"))
    cls = status if status in ("filled", "pending", "expired") else "pending"
    return f'<span class="leg {cls}"><i>{label}</i>{_esc(status)}</span>'


def _pos_status_pill(status: str) -> str:
    """④持仓状态 pill（原型 .st-open/.st-hold/.st-close + .d 圆点 + 中文标签；与②任务表分立）。"""
    mapping = {
        "pending_open": ("st-open", "建仓中"),
        "holding": ("st-hold", "持有"),
        "closed": ("st-close", "已清仓"),
    }
    cls, label = mapping.get(str(status or ""), ("st-hold", _esc(status)))
    return f'<span class="st {cls}"><span class="d"></span>{label}</span>'


def _axis_equity_svg(history: list[dict], *, width: int = 470, height: int = 150) -> str:
    """④权益曲线（带轴版）：NAV 网格线 + 刻度 + 末点标签（对齐原型 paper.html；服务端手搓）。"""
    navs = [float(p.get("nav") or 0) for p in history]
    if len(navs) < 2:
        return '<p class="muted">权益曲线待积累（≥2 个交易日点）。</p>'
    lo, hi = min(navs), max(navs)
    span = (hi - lo) or 1e-9
    pad_l, pad_r, pad_t, pad_b = 8, 46, 10, 18
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    x = lambda i: pad_l + i * plot_w / (len(navs) - 1)  # noqa: E731
    y = lambda v: pad_t + (1 - (v - lo) / span) * plot_h  # noqa: E731
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    for frac in (0.0, 0.5, 1.0):
        val = lo + frac * span
        gy = y(val)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" y2="{gy:.1f}" '
            'stroke="var(--line)" stroke-dasharray="3 4"/>'
            f'<text x="{pad_l + plot_w + 4}" y="{gy + 3.5:.1f}" font-size="9.5" '
            'fill="var(--t4)" font-family="var(--mono)">{val:.3f}</text>'
        )
    coords = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(navs))
    area = f"{pad_l},{pad_t + plot_h} " + coords + f" {pad_l + plot_w},{pad_t + plot_h}"
    parts.append(f'<polygon fill="rgba(88,166,255,.10)" points="{area}"/>')
    parts.append(f'<polyline fill="none" stroke="#58a6ff" stroke-width="1.6" points="{coords}"/>')
    dates = [str(p.get("date") or "") for p in history]
    for idx, anchor in ((0, "start"), (len(dates) - 1, "end")):
        if dates[idx]:
            tx = min(max(x(idx), pad_l), pad_l + plot_w)
            parts.append(
                f'<text x="{tx:.1f}" y="{height - 4}" font-size="9.5" fill="var(--t4)" '
                f'text-anchor="{anchor}" font-family="var(--mono)">{_esc(dates[idx][5:])}</text>'
            )
    lx, ly = x(len(navs) - 1), y(navs[-1])
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="#58a6ff"/>')
    parts.append(
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="6" fill="rgba(88,166,255,.25)"/>'
        f'<text x="{lx - 8:.1f}" y="{ly - 8:.1f}" font-size="10.5" fill="var(--t1)" '
        f'text-anchor="end" font-family="var(--mono)">{navs[-1]:.4f}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


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
    badge = (
        '<span class="chip chip-up">L2 开启</span>'
        if enabled
        else '<span class="chip chip-warn">L2 关闭</span>'
    )
    updated = state.get("updated_at") or ""
    updated_txt = f" · 更新 {_esc(updated)}" if updated else ""
    head = f"<p>{badge} · billing_mode {_esc(mode)}{updated_txt}</p>"
    note = (
        '<p class="tbl-note">开启后：T2-worthy monitor 事件自动触发盯盘复核（每日 ≤5 票、'
        "预算闸联动开启、probe 认证态判定计费形态）。关闭仅封 monitor 源自动任务，"
        "面板手动提交不受影响。</p>"
    )
    target = 'hx-target="#l2-settings" hx-swap="innerHTML"'
    if enabled:
        off_vals = 'hx-vals=\'{"enabled": "false"}\''
        btn = (
            f'<button class="mini" hx-post="/api/panel/l2" {off_vals} {target} '
            'hx-disabled-elt="this">关闭 L2</button>'
        )
    else:
        on_vals = 'hx-vals=\'{"enabled": "true"}\''
        confirm = (
            "hx-confirm='开启 L2 无人值守：monitor 事件将自动触发 T2 复核"
            "（每日 ≤5 票、预算闸联动开启）。确认？'"
        )
        btn = (
            f'<button class="mini" hx-post="/api/panel/l2" {on_vals} {target} {confirm} '
            'hx-disabled-elt="this">开启 L2</button>'
        )
    body = head + note + btn
    return (
        '<div class="card"><div class="card-h"><span class="ct">L2 无人值守总闸</span>'
        '<span class="cm">默认关 · 显式开启方生效</span></div>'
        f'<div class="card-b">{body}</div></div>'
    )


def render_stats_fragment(payload: dict) -> str:
    """面板⑥：kind 分布 + 任务成功率（样本不足标注）+ 预算双口径小表（§15）+ T+1 命中率。"""
    d = payload.get("decisions") or {}
    rows = "".join(
        f'<tr><td>{_esc(k)}</td><td class="num">{v}</td></tr>'
        for k, v in (d.get("kinds") or {}).items()
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
        '<table class="tbl"><colgroup><col><col class="w-num"></colgroup>'
        '<thead><tr><th scope="col">决策类型</th><th scope="col">计数</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>",
        "<h3>预算曲线（token + 等效美元双口径，F-34）</h3>",
    ]
    curve = payload.get("budget_curve") or []
    if curve:
        crows = "".join(
            "<tr>"
            f'<td class="num">{_esc(p.get("date"))}</td><td class="num">{p.get("sessions", 0)}</td>'
            f'<td class="num">{p.get("tokens", 0)}</td>'
            f'<td class="num">{float(p.get("cost_usd_equiv") or 0):.2f}</td>'
            "</tr>"
            for p in curve
        )
        parts.append(
            '<table class="tbl"><colgroup><col class="w-ts"><col class="w-num">'
            '<col class="w-num"><col class="w-num"></colgroup>'
            '<thead><tr><th scope="col">日</th><th scope="col">会话</th>'
            f'<th scope="col">token</th><th scope="col">等效 USD</th></tr></thead>'
            f"<tbody>{crows}</tbody></table>"
        )
    else:
        parts.append('<p class="muted">暂无会话记账。</p>')
    t1 = payload.get("t1_review") or {}
    if t1:
        t1_rate = t1.get("rate")
        t1_rate_text = f"{t1_rate:.0%}" if t1_rate is not None else "—"
        t1_note = f"（{_esc(t1.get('note'))}）" if t1.get("note") else ""
        parts.append("<h3>T+1 复核命中率（advice 卡 vs ledger 投影）</h3>")
        parts.append(
            f'<p class="muted">命中 {t1.get("hits", 0)} / 样本 {t1.get("sampled", 0)}'
            f" · 命中率 {t1_rate_text}{t1_note}——样本 &lt;30 只观察不记分（MIN_SAMPLES=30）</p>"
        )
    parts.append('<p class="muted">后验画像：全样本登记含亏损；模拟绩效均为假设性推演。</p>')
    return "".join(parts)


def render_briefing_fragment(payload: dict) -> str:
    """晨报/复盘卡区（0.0.18 分槽）：盘前晨报 / 盘后复盘两组 + legacy 通用 + ack 带槽位。"""
    day = payload.get("date", "")
    cards = payload.get("cards") or []
    key_levels = payload.get("key_levels") or {}

    def _card_html(card: dict) -> str:
        ack = bool(card.get("ack"))
        code = _esc(card.get("code", ""))
        slot = str(card.get("session") or "")
        slot_tag = f'<span class="tag">{slot}</span>' if slot else ""
        if ack:
            state = '<span class="st done">已确认</span>'
            action = ""
        else:
            state = '<span class="st queued">待确认</span>'
            vals = {"date": _esc(day), "code": code}
            if slot:
                vals["session"] = slot
            action = (
                '<button class="mini" hx-post="/api/panel/briefing/ack" '
                f"hx-vals='{json.dumps(vals, ensure_ascii=False)}' "
                'hx-target="#ov-brief" hx-swap="innerHTML" '
                'hx-disabled-elt="this">确认复核</button>'
            )
        levels = key_levels.get(card.get("code")) or {}
        chips = "".join(
            f'<div class="kchip"><span class="tag">{label}</span>'
            f'<span class="num">{_esc(value)}</span></div>'
            for label, value in (
                ("止损", levels.get("stop")),
                ("买点", levels.get("buy")),
                ("MA20", levels.get("ma20")),
            )
            if value is not None
        )
        chip_html = (
            f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin:4px 0">{chips}</div>'
            if chips
            else ""
        )
        body = _esc(str(card.get("md", ""))[:1200])
        return (
            f'<div class="task-card"><p><strong>{code}</strong> {slot_tag} · {state} {action}</p>'
            f"{chip_html}"
            f'<pre style="white-space:pre-wrap;font-size:12px;color:#8b949e">'
            f"{body}</pre></div>"
        )

    parts = [f"<h3>晨报 / 复盘行动项卡 · {day}</h3>"]
    if not cards:
        parts.append(
            '<p class="muted">当日无卡（L2 开启时盘前 09:00–09:29 / 盘后 18:15 起自动触发；'
            "或②页手动触发 briefing_morning / briefing_evening）。</p>"
        )
        return "".join(parts)
    for slot_key, title in (("am", "盘前晨报"), ("pm", "盘后复盘")):
        group = [c for c in cards if c.get("session") == slot_key]
        if group:
            parts.append(f'<h4 style="margin:10px 0 4px;font-size:12.5px">{title}</h4>')
            parts.extend(_card_html(c) for c in group)
    legacy = [c for c in cards if not c.get("session")]
    if legacy:
        parts.append(
            '<h4 style="margin:10px 0 4px;font-size:12.5px">通用（legacy briefing_review）</h4>'
        )
        parts.extend(_card_html(c) for c in legacy)
    return "".join(parts)


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
    """②页发起诊股表单：行式 label（label 同行 1/12 + Tooltip 说明，全局表单规范）。"""
    options = "".join(
        f'<option value="{k}">{label}</option>'
        for k, label in [
            ("diagnose_quick", "快速诊股"),
            ("diagnose_full", "全量诊股（双轨）"),
            ("batch_wave", "批量波次"),
            ("paper_confirm", "模拟盘逐笔确认（CC 辅助）"),
            ("briefing_morning", "盘前晨报（am 槽）"),
            ("briefing_evening", "盘后复盘（pm 槽）"),
            ("monitor_review", "盯盘复核（T2）"),
        ]
    )
    return (
        '<form class="frm" hx-post="/api/panel/submit" hx-target="#dg-result" '
        'hx-swap="innerHTML" hx-disabled-elt="find button">'
        '<div class="frm-row">'
        '<label for="dg-code" title="A 股 6 位 / 港股 5 位数字代码；预算与轮次由 kind 路由模板'
        '类型层强制（NR-4），锁/禁跑窗内提交将被拒（423）">证券代码</label>'
        '<input class="inp num" id="dg-code" name="code" placeholder="600519 / 00700" '
        'pattern="[0-9]{5,6}" required inputmode="numeric">'
        "</div>"
        '<div class="frm-row">'
        '<label for="dg-kind" title="六 kind 白名单：快速诊股 / 全量双轨 / 批量波次 / '
        '逐笔确认 / 晨报复盘 / 盯盘复核（T2）">诊股模式</label>'
        f'<select class="sel" id="dg-kind" name="kind">{options}</select>'
        "</div>"
        '<div class="frm-row">'
        '<label></label><button class="btn btn-pri" type="submit">开始</button></div>'
        '<p class="tbl-note">LLM 只产意见 · 聚合一律确定性裁决 · NR-3 断言器前置门 · '
        "锁/禁跑窗内提交将被拒（423）</p></form>"
        '<div id="dg-result" class="dg-result"></div>'
    )


def _ts_hhmm(ts: str) -> str:
    """①时间轴显示层：ISO 串取 HH:MM；非 ISO 短串原样（全值走 title Tooltip）。"""
    return ts[11:16] if len(ts) > 16 else ts


def render_event_feed(payload: dict) -> str:
    """①今日事件流：时间轴形态（T 徽章/mech_risk riskchip/风险行高亮/现价与距线）。"""
    events = (payload.get("events") or [])[-10:]
    if not events:
        return (
            '<p class="loadmsg">暂无盯盘事件（watch.py 台账为空或未运行）。</p>'
            '<span id="ov-ev-count" hx-swap-oob="true">0 条</span>'
        )
    rows = []
    for ev in events:
        facts = ev.get("facts") or {}
        price = facts.get("price") or facts.get("pct")
        mech = ev.get("mech_risk") or {}
        risk_cls = " risk" if mech else ""
        riskchip = (
            f'<span class="riskchip">mech_risk {_esc(mech.get("severity", ""))}</span>'
            if mech
            else ""
        )
        price_txt = f"现价 {price}" if price is not None else ""
        review_txt = f" · {ev['review']}" if ev.get("review") else ""
        ts = str(ev.get("ts", ""))
        rows.append(
            f'<div class="ev{risk_cls}"><div class="ev-time num" title="{_esc(ts)}">'
            f"{_esc(_ts_hhmm(ts))}</div>"
            f'<div class="ev-rail"><div class="ev-dot"></div></div>'
            f'<div class="ev-name"><span class="ev-stock"><span class="code num">'
            f"{_esc(ev.get('code', ''))}</span> {_esc(ev.get('rule', ''))}</span>"
            f'<span class="ev-top">{_esc(price_txt)} {riskchip}'
            f'<span class="lv lv0">{_esc(ev.get("level", "T0"))}</span>'
            f"{_esc(review_txt)}</span></div></div>"
        )
    return "".join(rows) + f'<span id="ov-ev-count" hx-swap-oob="true">{len(events)} 条</span>'


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
        "告警主通道永久留服务端（后台标签页冻结，I-48）· 锁屏不保证送达——离机触达按面板"
        "常驻拉取式主动查看设计（方案 §1）——本卡为只读历史重放</p>"
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
            f'<div class="step {cls}"><div class="sc">{int(st["stage"])}</div>'
            f'<div class="sl">{_esc(st["name"])}</div>'
            f'<div class="ss num">{_esc(st.get("note", ""))}</div></div>'
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
            f'<div><span class="sig {sig_map.get(c.get("signal"), "")}">'
            f'<span class="d"></span>{_esc(c.get("signal") or "")}</span> '
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
            f'<td><span class="sig {sig_cls}"><span class="d"></span>'
            f"{_esc(signal or row.get('signal_raw'))}</span></td>"
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
        return (
            '<p class="loadmsg">无待办。</p><span id="ov-todo-count" hx-swap-oob="true">0 项</span>'
        )
    rows = []
    for item in todos:
        rows.append(
            f'<div class="todo"><div class="cbx"></div><div style="flex:1;min-width:0">'
            f'<span class="code num">{_esc(item["code"])}</span> {_esc(item["text"])}</div>'
            f'<span class="chip chip-warn">{_esc(item["chip"])}</span></div>'
        )
    return "".join(rows) + f'<span id="ov-todo-count" hx-swap-oob="true">{len(todos)} 项</span>'


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


def render_syscard(status: dict, sessions: dict, queue: dict, l2_state: dict | None = None) -> str:
    """①系统状态卡：三槽调度时刻+分级+CC 行（探针时刻）+在跑会话。"""
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
    probe_at = (l2_state or {}).get("updated_at") or ""
    probe_html = (
        f'<span class="sys-sched num" title="probe 认证态判定时刻">probe {probe_at[:19]}</span>'
        if probe_at
        else ""
    )
    rows.append(
        f'<div class="sys-row"><div class="sdot {"sdot-ok" if cc_active else "sdot-wait"}"></div>'
        '<div style="flex:1;min-width:0"><span class="sys-name">CC 大脑</span>'
        '<div class="sys-desc">headless 子进程唯一 LLM 通道</div></div>'
        f"{probe_html}"
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
    """面板④：NR-2 常驻条 + 持仓表（名称列/状态 pill/建仓批 .leg/可卖(T+1)/盈亏红涨绿跌/
    建仓中高亮）+ 表尾汇总条 + 逐笔确认队列 + 绩效三格 + 带轴权益曲线（原型 paper.html 对齐）。

    NR-5 呈现态豁免（13-paper §⑥）：面板可显份额原值（导出/落盘卡仍降级脱敏）。
    """
    parts = []
    if notice:
        parts.append(f'<p class="ok">{_esc(notice)}</p>')
    parts.append(
        '<p class="warn"><strong>NR-2</strong>：模拟盘永不接真实下单接口；'
        "全部模拟绩效为假设性推演（含费用与滑点）。</p>"
    )
    names = payload.get("names") or {}
    positions = payload.get("positions") or []
    if positions:
        rows = []
        for pos in positions:
            badges = " ".join(_tranche_badge(t) for t in pos.get("tranches", []))
            last = pos.get("last_price") or 0.0
            cost = pos.get("avg_cost") or 0.0
            pnl_pct = round((last - cost) / cost * 100, 2) if cost and last else None
            pnl_cls = "pf" if (pnl_pct or 0) > 0 else ("nf" if (pnl_pct or 0) < 0 else "")
            pnl_html = (
                f'<span class="num {pnl_cls}">{pnl_pct:+.2f}%</span>'
                if pnl_pct is not None
                else "—"
            )
            open_cls = ' class="open"' if str(pos.get("status")) == "pending_open" else ""
            name = names.get(pos["code"])
            name_html = f" {_esc(name)}" if name else ""
            rows.append(
                f"<tr{open_cls}>"
                f'<td><span class="code num">{_esc(pos["code"])}</span>{name_html}</td>'
                f"<td>{_pos_status_pill(pos['status'])}</td>"
                f"<td>{badges}</td>"
                f'<td class="num">{pos.get("lots", 0)}</td>'
                f'<td class="num">{pos.get("sellable_lots", 0)}</td>'
                f'<td class="num">{_esc(pos.get("avg_cost"))}</td>'
                f'<td class="num">{_esc(pos.get("last_price"))}</td>'
                f"<td>{pnl_html}</td>"
                f'<td class="num">{_esc(round(pos.get("market_value") or 0, 2))}</td>'
                "</tr>"
            )
        totals = payload.get("totals") or {}
        parts.append(
            '<table class="tbl"><colgroup><col class="w-code"><col class="w-st">'
            '<col class="w-batch"><col class="w-num"><col class="w-num">'
            '<col class="w-num"><col class="w-num"><col class="w-num"><col class="w-num">'
            '</colgroup><thead><tr><th scope="col">股票</th><th scope="col">状态</th>'
            '<th scope="col">建仓批</th><th scope="col">手数</th><th scope="col">可卖(T+1)</th>'
            '<th scope="col">成本价</th><th scope="col">现价</th><th scope="col">盈亏%</th>'
            f'<th scope="col">市值</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            '<p class="tbl-note">总市值 '
            f"{totals.get('market_value', 0):,.2f} · 现金 {totals.get('cash', 0):,.2f} · "
            f"总资产 {totals.get('total', 0):,.2f} · 净值 {totals.get('nav', 0):.4f}"
            "（盈亏口径：红涨绿跌）</p>"
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
                'hx-swap="innerHTML" hx-disabled-elt="this">确认成交</button>'
            )
            reject_btn = (
                f'<button class="mini" hx-post="/api/panel/paper/reject" '
                f'hx-vals=\'{{"request_id": "{rid}"}}\' hx-target="#paper" '
                'hx-swap="innerHTML" hx-disabled-elt="this">驳回</button>'
            )
            rows.append(
                "<tr>"
                f'<td class="num" title="{_esc(req.get("ts", ""))}">{_esc(req.get("ts", ""))}</td>'
                f'<td><span class="code num">{_esc(req.get("code", ""))}</span></td>'
                f"<td>{_esc(req.get('tranche_leg', ''))}</td>"
                f"<td>{_esc(req.get('side', ''))}</td>"
                f'<td class="num">{_esc(req.get("lots"))}</td>'
                f'<td class="num">{_esc(req.get("ref_price"))}</td>'
                f'<td class="num">{_esc(req.get("bar_date", ""))}</td>'
                f"<td>{confirm_btn} {reject_btn}</td>"
                "</tr>"
            )
        parts.append(
            '<table class="tbl"><colgroup><col class="w-ts"><col class="w-code">'
            '<col class="w-batch"><col class="w-num"><col class="w-num">'
            '<col class="w-num"><col class="w-ts"><col class="w-op"></colgroup>'
            '<thead><tr><th scope="col">时间</th><th scope="col">代码</th>'
            '<th scope="col">批</th><th scope="col">方向</th><th scope="col">手数</th>'
            '<th scope="col">参考价</th><th scope="col">bar 日</th>'
            f'<th scope="col">操作</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
        )
    else:
        parts.append('<p class="muted">队列为空。</p>')

    perf = payload.get("perf")
    if perf is not None:  # 复用 0.0.15 _perf_grid（单一渲染件，勿并存两版绩效三格）
        note = perf.get("note") or "样本 ≥30 可记分"
        parts.append(_perf_grid(perf))
        parts.append(
            f'<p class="stat-note">模拟绩效 · 假设性推演 · 含费用与滑点 · 平仓 '
            f"{int(perf.get('closed_pairs', 0))} 笔 · {_esc(note)}</p>"
        )

    parts.append("<h3>权益曲线（NAV = 权益 / 预算）</h3>")
    history = payload.get("equity_history") or []
    parts.append(_axis_equity_svg(history))
    return "".join(parts)


def _perf_grid(perf: dict) -> str:
    """④绩效三格（Sharpe/最大回撤/胜率；None 显 —；回撤负值绿=跌语义）。"""
    sharpe = perf.get("sharpe")
    max_dd = perf.get("max_drawdown")
    win_rate = perf.get("win_rate")
    dd_v = (
        f'<div class="v dn">{max_dd * 100:.1f}%</div>'
        if max_dd is not None
        else '<div class="v">—</div>'
    )
    wr_v = (
        f'<div class="v">{win_rate:.0%}</div>' if win_rate is not None else '<div class="v">—</div>'
    )
    return (
        '<div class="stats">'
        f'<div class="stat"><div class="k">Sharpe（日频年化）</div>'
        f'<div class="v">{sharpe if sharpe is not None else "—"}</div></div>'
        f'<div class="stat"><div class="k">最大回撤</div>{dd_v}</div>'
        f'<div class="stat"><div class="k">胜率（FIFO）</div>{wr_v}</div>'
        "</div>"
    )


_ORIGIN_ZH = {
    "gate": "准入闸拒（L2 总闸 / 每日 5 票 / 实盘优先）",
    "assert": "断言器拒（schema / 红线）",
    "render_scan": "落盘扫描拒（脱敏断言）",
    "write_failure": "写卡失败",
    "session_degraded": "会话降级",
}


def render_advice_library(payload: dict) -> str:
    """面板⑤：建议卡库（当日卡列表，code 可点开详情四块）+ 拒卡/降级通报区。"""
    cards = payload.get("cards") or []
    refused = payload.get("refused") or []
    day = payload.get("date", "")
    parts = [f"<h3>建议卡 · {_esc(day)}</h3>"]
    if cards:
        day = payload.get("date") or ""
        rows = []
        for card in cards:
            code = _esc(card.get("code", ""))
            link = (
                f'<a href="#" class="code num" '
                f'hx-get="/api/panel/advice-detail/{_esc(day)}/{code}" '
                f'hx-target="#advice-detail-2" hx-swap="innerHTML">{code}</a>'
            )
            rows.append(
                "<tr>"
                f"<td>{link}</td>"
                f'<td title="{_esc(card.get("candidate_action", ""))}">'
                f"{_esc(card.get('candidate_action', ''))}</td>"
                f"<td>{_esc(card.get('final_signal', ''))}</td>"
                f"<td>{_esc(card.get('prev_final_signal', '') or '—')}</td>"
                f'<td class="num">{_esc(card.get("t1_sell_pct", ""))}% · '
                f"{_esc(card.get('t1_sell_tier'))}</td>"
                f'<td class="num">{_esc(card.get("mtime", ""))}</td>'
                "</tr>"
            )
        parts.append(
            '<table class="tbl"><colgroup><col class="w-code"><col><col class="w-num">'
            '<col class="w-num"><col class="w-num"><col class="w-ts"></colgroup>'
            '<thead><tr><th scope="col">代码</th><th scope="col">候选动作</th>'
            '<th scope="col">终判</th><th scope="col">上一裁决</th>'
            '<th scope="col">T+1 可卖</th><th scope="col">落盘</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        parts.append('<p class="muted">当日无建议卡（T2 复核未触发或被降级）。</p>')
    parts.append("<h3>拒卡 / 降级通报</h3>")
    if refused:
        rows = []
        for item in refused:
            origin = item.get("origin", "")
            note = (item.get("facts_note") or "")[:80]
            rows.append(
                "<tr>"
                f'<td class="num" title="{_esc(item.get("ts", ""))}">'
                f"{_esc(item.get('ts_short') or item.get('ts', ''))}</td>"
                f'<td><span class="code num">{_esc(item.get("code", ""))}</span></td>'
                f'<td title="{_esc(_ORIGIN_ZH.get(origin, origin))}">{_esc(origin or "—")}</td>'
                f'<td title="{_esc(item.get("reason_code", ""))}">'
                f"{_esc(item.get('reason_code', '') or '—')}</td>"
                f'<td title="{_esc(note)}">{_esc(note or "—")}</td>'
                "</tr>"
            )
        parts.append(
            '<table class="tbl"><colgroup><col class="w-ts"><col class="w-code">'
            '<col class="w-num"><col class="w-kind"><col></colgroup>'
            '<thead><tr><th scope="col">时间</th><th scope="col">代码</th>'
            '<th scope="col">来源</th><th scope="col">原因</th><th scope="col">通报</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
    else:
        parts.append('<p class="muted">无拒卡/降级记录。</p>')
    parts.append(
        '<p class="muted">卡面脱敏口径：只落百分比与档位词（份额原值禁入落盘产物，R2/NR-5）；'
        "候选动作为 vibe-stock/04 五档分类学措辞。</p>"
    )
    return "".join(parts)


def render_sessions_table(sessions: dict) -> str:
    """⑦会话登记表：uuid/task_id/tokens/等效 USD/转录路径（审计三件套的 sessions 腿呈现）。

    固定列宽 + 单行省略 + title 悬浮全值（UI 表格规范）；
    sessions/budget 属运营元数据（NR-5 豁免面）。
    """
    rows_map = (sessions.get("sessions") or {}) if isinstance(sessions, dict) else {}
    records = sorted(rows_map.values(), key=lambda r: str(r.get("ts", "")), reverse=True)
    if not records:
        return '<p class="muted">暂无会话登记。</p>'
    trs = []
    for r in records[:30]:
        tokens = (r.get("usage_tokens") or {}).get("total", 0)
        uuid = str(r.get("session_uuid", ""))
        task = str(r.get("task_id", ""))
        transcript = str(r.get("transcript_path", ""))
        trs.append(
            "<tr>"
            f'<td class="num">{_esc(r.get("ts", ""))}</td>'
            f'<td class="num" title="{_esc(uuid)}">{_esc(uuid[:13])}…</td>'
            f'<td class="num" title="{_esc(task)}">{_esc(task[:20])}…</td>'
            f'<td class="num">{tokens:,}</td>'
            f'<td class="num">{r.get("cost_usd_equiv", 0)}</td>'
            f'<td class="num" title="{_esc(transcript)}">{_esc(transcript[:28])}…</td>'
            "</tr>"
        )
    return (
        '<table class="tbl"><colgroup><col style="width:150px"><col style="width:120px">'
        '<col style="width:160px"><col style="width:90px"><col style="width:90px">'
        "<col></colgroup><thead><tr><th>ts</th><th>session</th><th>task</th>"
        "<th>tokens</th><th>等效 USD</th><th>转录路径</th></tr></thead><tbody>"
        f"{''.join(trs)}</tbody></table>"
        '<p class="tbl-note">三件套互查：decisions（hash 链）× sessions × audit/ stream-json 归档'
        "（30 天官方清理，F-38）</p>"
    )


def render_submit_result(task: dict | None, *, status: int, detail: str) -> str:
    if task is not None and status < 300:
        return f'<div id="submit-result">{render_task_card(task)}</div>'
    return (
        f'<div class="task-card" id="submit-result">'
        f'<p class="warn">提交被拒（{status}）：{_esc(detail)}</p></div>'
    )
