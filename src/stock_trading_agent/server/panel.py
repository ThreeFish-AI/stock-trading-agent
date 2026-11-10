"""面板 HTML fragment 渲染（服务端手搓，html.escape 全转义防注入；零模板依赖，§16）。

htmx 消费这些 fragment：常态 180s 轮询、任务卡非终态 5s 快轮询（终态停）。
NR-5：只透出脱敏态数据（monitor events/universe 本身脱敏；sessions/budget 为运营元数据）。
"""

from __future__ import annotations

import html
import json
import re

TERMINAL_STATUSES = frozenset({"done", "failed", "abandoned"})

# 信号词 → CSS 类（意见板/信号表同源 SSOT，站点内勿再建映射）
_SIG_CLASS = {
    "strong_buy": "sig-sb",
    "buy": "sig-b",
    "hold": "sig-h",
    "sell": "sig-s",
    "strong_sell": "sig-ss",
}


def _status_pill(status: str) -> str:
    cls = _esc(status)
    return f'<span class="st {cls}">{cls}</span>'


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _table(cols: str, heads: str, body: str) -> str:
    """表格骨架 SSOT（.tbl/colgroup/thead/tbody 全仓零漂移）。

    cols：`|` 分隔的 colgroup 宽类（`-` = 无类列）；heads：`|` 分隔表头（`:num` 后缀 =
    数值列）。body 为已组装的 `<tr>` 行串（转义责任在调用方，沿各站点现惯例）。
    """
    colgroup = "".join(f'<col class="{c}">' if c != "-" else "<col>" for c in cols.split("|"))
    thead = "".join(
        f'<th scope="col" class="num">{h[:-4]}</th>'
        if h.endswith(":num")
        else f'<th scope="col">{h}</th>'
        for h in heads.split("|")
    )
    return (
        f'<table class="tbl"><colgroup>{colgroup}</colgroup>'
        f"<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"
    )


def _card(title: str, body: str, *, meta: str = "", cls: str = "") -> str:
    """卡片壳 SSOT：title/body 传已组装 HTML；meta 为 `显示文案|悬浮全值`（无悬浮则省 `|` 段）。"""
    meta_html = ""
    if meta:
        text, _, tip = meta.partition("|")
        meta_html = (
            f'<span class="cm" title="{tip}">{text}</span>'
            if tip
            else f'<span class="cm">{text}</span>'
        )
    return (
        f'<div class="card{f" {cls}" if cls else ""}"><div class="card-h">'
        f'<span class="ct">{title}</span>{meta_html}</div>'
        f'<div class="card-b">{body}</div></div>'
    )


def _empty(text: str, tip: str = "", *, title: str = "") -> str:
    """空态 SSOT：text/tip/title 传已组装 HTML（含 `<a>` 引导链等，沿站点现惯例不二次转义）。"""
    tip_html = f'<span class="empty-tip">{tip}</span>' if tip else ""
    head = f'<p class="empty" title="{title}">' if title else '<p class="empty">'
    return f"{head}{text}{tip_html}</p>"


def _kchip(key: str, value: str) -> str:
    return f'<span class="kchip"><i>{key}</i> <b>{value}</b></span>'


def _stat_grid(cells: tuple[tuple[str, str, str], ...], *, label: str, cls: str = "") -> str:
    """KPI 网格 SSOT：cells = (指标名, 值 HTML, 值附加类) 三元组（附加类如 "dn"）。"""
    body = "".join(
        f'<div class="stat" role="listitem"><div class="k">{k}</div>'
        f'<div class="v{f" {v_cls}" if v_cls else ""}">{v}</div></div>'
        for k, v, v_cls in cells
    )
    stats_cls = f"stats {cls}" if cls else "stats"
    return f'<div class="{stats_cls}" role="list" aria-label="{label}">{body}</div>'


def _name_tail(names: dict, code: str) -> str:
    """代码后缀名称（缺席空串）：三处股票列同源。"""
    name = names.get(code)
    return f" {_esc(name)}" if name else ""


def _money_td(value) -> str:
    """金额格（§⑨ 千分位 + 两位小数；title 挂全值；缺数 —）。"""
    if value is None:
        return '<td class="num">—</td>'
    return f'<td class="num" title="{float(value):,.2f}">{float(value):,.2f}</td>'


def render_budget_line(budget: dict) -> str:
    if not isinstance(budget, dict) or "tasks" not in budget:
        return '<p class="muted">预算台账未初始化（默认关，仅记账）。</p>'
    tokens = budget.get("tokens") if isinstance(budget.get("tokens"), dict) else {}
    return (
        f'<p class="muted" title="订阅轨双口径记账（F-34）">模式 '
        f"{_esc(budget.get('billing_mode', 'subscription'))} · "
        f"任务数 {int(budget.get('tasks', 0))} · "
        f"token 累计 {int(tokens.get('total', 0))} · "
        f"等效 USD {budget.get('cost_usd_equiv', 0)}（订阅轨双口径记账）</p>"
    )


def render_events_table(payload: dict, *, tail: int = 50) -> str:
    """③盯盘事件表（原型七列 + mech_risk）：现价/距线来自 events facts，名称来自注入。

    advice_codes（当日建议卡 stem 集）在场的行给「详情」按钮，填充③页 #advice-detail；
    距线 = price/level − 1，≤0 标 breach、≤3% 标 near（与①信号卡同判据）。
    0.0.24：表前异常摘要 chips（零新 IO）；复核按钮带 ctx=mo（③页发起不写②#dg-session）；
    详情/复核注入带 show:top 滚动定位；mech_risk 分 sev-hi/sev-md 两档视觉。
    """
    events = (payload.get("events") or [])[-tail:]
    if not events:
        return _empty(
            "今日暂无盯盘事件。",
            "开盘时段自动盯盘；如持续为空，检查盯盘进程是否在跑（①页右列系统状态卡）。",
            title="watch.py 台账为空或未运行",
        )
    names = payload.get("names") or {}
    advice_codes = set(payload.get("advice_codes") or [])
    day = payload.get("date") or ""
    n_breach = n_near = n_mech = n_sup = 0
    parsed = []
    has_dist = False
    for ev in events:
        code = ev.get("code", "*")
        review = ev.get("review") or ""
        suppress = ev.get("suppress_reason") or ""
        suppressed = bool(suppress)
        if suppressed:
            n_sup += 1
        facts = ev.get("facts") or {}
        price = facts.get("price")
        level = facts.get("level")
        dist_txt = dist_cls = ""
        if isinstance(price, (int, float)) and isinstance(level, (int, float)) and level:
            dist = (price / level - 1) * 100
            dist_txt = f"{dist:+.2f}%"
            dist_cls = "dist-breach" if dist <= 0 else ("dist-near" if dist <= 3 else "")
            if dist <= 0:
                n_breach += 1
            elif dist <= 3:
                n_near += 1
        if dist_txt:
            has_dist = True
        mech = ev.get("mech_risk") or {}
        if mech:
            n_mech += 1
        sev = str(mech.get("severity", ""))
        sev_cls = " sev-hi" if (mech and sev == "high") else (" sev-md" if mech else "")
        riskchip = f'<span class="riskchip{sev_cls}">mech_risk {_esc(sev)}</span>' if mech else ""
        action = ""
        if code in advice_codes and day:
            action += (
                f'<button class="mini" hx-get="/api/panel/advice-detail/{_esc(day)}/{_esc(code)}'
                '?mount=mo" hx-target="#advice-detail" hx-swap="innerHTML show:top">详情</button> '
            )
        if review and not suppressed:
            action += (
                f'<button class="mini" hx-post="/api/panel/submit" '
                f'hx-vals=\'{{"kind": "monitor_review", "code": "{_esc(code)}", "ctx": "mo"}}\' '
                f'hx-target="#advice-detail" hx-swap="innerHTML show:top" '
                f'hx-disabled-elt="this">盯盘复核 {_esc(code)}</button>'
            )
        elif suppressed:
            action = '<span class="tag">已压制</span>'
        ts = str(ev.get("ts", ""))
        name = names.get(code)
        stock_tip = _esc(f"{code} {name}" if name else code)
        dist_td = f'<td class="num {_esc(dist_cls)}">{dist_txt}</td>' if has_dist else ""
        parsed.append(
            "<tr>"
            f'<td class="num" title="{_esc(ts)}">{_esc(_ts_hhmm(ts))}</td>'
            f'<td title="{stock_tip}"><span class="code num">{_esc(code)}</span>'
            f"{_name_tail(names, code)}</td>"
            f'<td title="{_esc(ev.get("rule", ""))}">{_esc(ev.get("rule", ""))}</td>'
            f'<td class="num">{_fmt(price)}</td>'
            f"{dist_td}"
            f'<td><span class="lv {_lv_class(ev.get("level", "T0"))}"'
            f">{_esc(ev.get('level', 'T0'))}</span></td>"
            f"<td>{riskchip or '—'}</td>"
            f'<td title="{_esc(review)}">{_esc(review or "—")}</td>'
            f'<td title="{_esc(suppress)}">{_esc(suppress or "—")}</td>'
            f'<td><div class="tbl-act">{action}</div></td>'
            "</tr>"
        )
    rows = parsed
    chips = "".join(
        _kchip(k, str(v))
        for k, v in (
            ("事件", len(events)),
            ("breach", n_breach),
            ("near", n_near),
            ("mech_risk", n_mech),
            ("已压制", n_sup),
        )
    )
    summary = f'<div class="chips">{chips}</div>'
    dist_parts = ["w-num"] if has_dist else []
    dist_heads = ["距线:num"] if has_dist else []
    # 0.0.34：级别列固定窄轨（T0 徽章 2ch）——auto 等分时 179px 虚胖挤压事件/风险/短语列
    cols = "|".join(
        ["w-ts", "w-stock", "-", "w-num", *dist_parts, "w-lv", "-", "-", "w-sup", "w-op"]
    )
    heads = "|".join(
        [
            "时间:num",
            "股票",
            "事件",
            "现价:num",
            *dist_heads,
            "级别",
            "机械风险",
            "复核短语",
            "压制",
            "操作",
        ]
    )
    return (
        summary
        + '<div class="tbl-wrap sm">'
        + _table(cols, heads, "".join(rows))
        + "</div>"
        + '<p class="tbl-note" title="盯盘轮询零 LLM · 事件语义对齐 vibe-stock/07 · 盘中结论以'
        '收盘复核为准（R9）">盯盘轮询零 LLM · 事件语义对齐 vibe-stock/07 · '
        "盘中结论以收盘复核为准</p>"
    )


def render_tasks_table(queue: dict) -> str:
    tasks = queue.get("tasks") or []
    if not tasks:
        return (
            _empty("队列为空。", "②页表单或③页复核按钮提交后于此跟踪。")
            + '<span id="dg-task-meta" hx-swap-oob="true" class="cm">0 项</span>'
        )
    rows = []
    for t in tasks[-20:]:
        err = t.get("error") or ""
        enq = str(t.get("enqueued_at", ""))
        rows.append(
            "<tr>"
            f"<td>{_esc(t.get('kind', ''))}</td>"
            f'<td><span class="code num">{_esc(t.get("code", ""))}</span></td>'
            f"<td>{_status_pill(t.get('status', ''))}</td>"
            f'<td class="num" title="{_esc(enq)}">{_esc(_ts_hhmm(enq))}</td>'
            f'<td title="{_esc(err)}">{_esc(err or "—")}</td>'
            "</tr>"
        )
    return (
        _table("w-kind|w-code|w-st|w-ts|-", "类型|代码|状态|入队:num|错误", "".join(rows))
        + f'<span id="dg-task-meta" hx-swap-oob="true" class="cm">{len(tasks)} 项</span>'
    )


def render_task_card(task: dict, *, session_target: str | None = "#dg-session") -> str:
    """任务卡：非终态带 5s 快轮询；终态停轮询并展示结果。

    0.0.13：有 session_uuid 时同步驱动②页「会话观测」区（同卡轮询双写，P2 案 a）。
    session_target=None（③页发起的复核）：不持 hook，note 给「在②页观测会话」跳转——
    消解③操作/②观测的跨页分裂与双任务争写 #dg-session。
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
    if session_uuid and session_target:
        session_hook = (
            f' hx-get="/api/panel/session/{task_id}" hx-trigger="every 5s" '
            f'hx-target="{_esc(session_target)}" hx-swap="innerHTML"'
        )
    note = ""
    if status == "done":
        note = (
            '<p class="ok">任务完成（会话产物已过断言器落盘；报告见 vibe-stock reports 目录）。</p>'
        )
    elif status in ("failed", "abandoned"):
        note = f'<p class="warn">任务 {_esc(status)}：{_esc(task.get("error") or "")}</p>'
    if session_target is None:
        note += (
            '<p><button class="mini" onclick="location.hash=\'#/diagnosis\'">'
            "在②页观测会话</button></p>"
        )
    return (
        f'<div class="task-card" id="task-card" {poll}{session_hook}>'
        f"<p><strong>{_esc(task.get('kind', ''))}</strong> · {_esc(task.get('code', ''))} · "
        f'<span class="st {_esc(status)}">{_esc(status)}</span></p>'
        f'<p class="muted">task_id {_esc(task.get("task_id", ""))}</p>'
        f"{note}</div>"
    )


def render_audit(
    decisions: list[dict],
    budget: dict,
    *,
    chain_ok: bool | None = None,
    decisions_total: int | None = None,
) -> str:
    """⑦decisions 台账（0.0.24：链校验/预算摘要 chips 升卡首；head tag 标链尾行）。

    decisions_total：台账全量行数（kchip「台账」计数；decisions 入参为 tail 窗口，
    缺省回退窗口长——0.0.34 前误把窗口数当总量展示）。
    """
    rows = []
    tail = decisions[-20:]
    for line in tail:
        payload = line.get("payload") or {}
        summary = payload.get("kind") or payload.get("reason") or ""
        is_head = line is tail[-1] if tail else False
        head_tag = ' <span class="tag">head</span>' if is_head else ""
        ts = str(line.get("ts", ""))
        h = str(line.get("hash", ""))
        rows.append(
            "<tr>"
            f'<td class="num">{int(line.get("seq", 0))}</td>'
            f'<td class="num" title="{_esc(ts)}">{_esc(_ts_short(ts))}</td>'
            f"<td>{_esc(line.get('kind', ''))}</td>"
            f'<td title="{_esc(summary)}">{_esc(summary or "—")}</td>'
            f'<td class="num" title="{_esc(h)}">{_esc(h[:12])}…{head_tag}</td>'
            "</tr>"
        )
    chain_note = ""
    summary_chips = ""
    tokens = budget.get("tokens") if isinstance(budget.get("tokens"), dict) else {}
    count_chips = "".join(
        _kchip(k, str(v))
        for k, v in (
            ("台账", decisions_total if decisions_total is not None else len(decisions)),
            ("token 累计", int(tokens.get("total", 0))),
            ("等效 USD", budget.get("cost_usd_equiv", 0)),
        )
    )
    if chain_ok is True:
        chain_note = '<p class="ok">链校验通过 · 台账防篡改完好</p>'
        summary_chips = (
            f'<div class="chips"><span class="chip chip-up">链校验通过</span>{count_chips}</div>'
        )
    elif chain_ok is False:
        from stock_trading_agent.state.decisions import REPAIR_HINT

        chain_note = (
            '<p class="warn">hash 链校验失败（seq 乱序或 hash 复算不符）——'
            f"台账可能被改写或并发写入，{_esc(REPAIR_HINT)}</p>"
        )
        summary_chips = (
            f'<div class="chips"><span class="chip chip-warn">链校验失败</span>{count_chips}</div>'
        )
    table = (
        '<div class="tbl-wrap">'
        + _table(
            "w-seq|w-ts|w-kind|-|w-hash", "序号:num|时间:num|类型|摘要|hash 链:num", "".join(rows)
        )
        + "</div>"
        if rows
        else _empty("decision log 为空。", "首条决策写入后于此呈现。")
    )
    body = summary_chips + table + chain_note + render_budget_line(budget)
    return _card("decisions 台账（尾部 20 条）", body, meta="hash 链防篡改 · 只读投影")


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
    """④权益曲线（带轴版）：NAV 网格线 + 刻度 + 末点标签（对齐原型 paper.html；服务端手搓）。

    0.0.24：单 style 属性合并（此前双 style 属性致 font-family 恒失效）；
    <2 点时渲静态空轴骨架（布局稳定 + 传达预期）。
    """
    navs = [float(p.get("nav") or 0) for p in history]
    if len(navs) < 2:
        # 空轴骨架：左右内边距对称（无轴标签可预留），网格与提示文字按画布真居中
        pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 18
        plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
        return (
            f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-label="权益曲线待积累（≥2 个交易日点）">'
            + "".join(
                f'<line x1="{pad_l}" y1="{pad_t + frac * plot_h:.1f}" '
                f'x2="{pad_l + plot_w}" y2="{pad_t + frac * plot_h:.1f}" '
                'style="stroke:var(--line)" stroke-dasharray="3 4"/>'
                for frac in (0.0, 0.5, 1.0)
            )
            + f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" text-anchor="middle" '
            'style="font-size:var(--fs-11);fill:var(--t4);font-family:var(--mono)">'
            "待积累（≥2 个交易日点）</text></svg>"
        )
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
            'style="stroke:var(--line)" stroke-dasharray="3 4"/>'
            f'<text x="{pad_l + plot_w + 4}" y="{gy + 3.5:.1f}" '
            'style="font-size:var(--fs-10);fill:var(--t4);font-family:var(--mono)">'
            f"{val:.3f}</text>"
        )
    coords = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(navs))
    area = f"{pad_l},{pad_t + plot_h} " + coords + f" {pad_l + plot_w},{pad_t + plot_h}"
    parts.append(
        f'<polygon style="fill:color-mix(in srgb,var(--acc) 10%,transparent)" points="{area}"/>'
    )
    parts.append(
        f'<polyline fill="none" style="stroke:var(--acc)" stroke-width="1.6" points="{coords}"/>'
    )
    dates = [str(p.get("date") or "") for p in history]
    for idx, anchor in ((0, "start"), (len(dates) - 1, "end")):
        if dates[idx]:
            tx = min(max(x(idx), pad_l), pad_l + plot_w)
            parts.append(
                f'<text x="{tx:.1f}" y="{height - 4}" text-anchor="{anchor}" '
                'style="font-size:var(--fs-10);fill:var(--t4);font-family:var(--mono)">'
                f"{_esc(dates[idx][5:])}</text>"
            )
    lx, ly = x(len(navs) - 1), y(navs[-1])
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" style="fill:var(--acc)"/>')
    # 0.0.34：末点标签基线钳制——末点为区间最高时 ly-8 越上内边距，ascent 被 viewBox 裁切
    ty = max(ly - 8, pad_t + 8)
    parts.append(
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="6" '
        'style="fill:color-mix(in srgb,var(--acc) 25%,transparent)"/>'
        f'<text x="{lx - 8:.1f}" y="{ty:.1f}" text-anchor="end" '
        f'style="font-size:var(--fs-11);fill:var(--t1);font-family:var(--mono)">'
        f"{navs[-1]:.4f}</text>"
    )
    parts.append("</svg>")
    return "".join(parts)


def _sparkline_svg(
    values: list[float], *, aria_label: str, width: int = 220, height: int = 44, fluid: bool = False
) -> str:
    """序列 sparkline（服务端手搓内联 SVG，零 CDN/零前端库；aria-label 必填）。

    fluid=True：svg 满铺容器宽（①NAV 卡等单列挂点，防固定 220px 左挂右侧死区）；
    固定宽度形态保留给 ⑥页 .svgs 双 sparkline 并排场景。
    """
    if len(values) < 2:
        return '<p class="empty">曲线待积累（≥2 个数据点）。</p>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1e-9
    step = width / (len(values) - 1)
    coords = [
        f"{i * step:.1f},{height - (v - lo) / span * (height - 6) - 3:.1f}"
        for i, v in enumerate(values)
    ]
    fluid_attr = ' style="display:block;width:100%;height:auto"' if fluid else ""
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"'
        f'{fluid_attr} role="img" aria-label="{_esc(aria_label)}">'
        f'<polyline fill="none" stroke-width="1.5" style="stroke:var(--acc)" '
        f'points="{" ".join(coords)}"/>'
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
    head = (
        f'<p>{badge} · <span title="billing_mode（probe 认证态判定）">计费形态</span>'
        f" {_esc(mode)}{updated_txt}</p>"
    )
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
    return _card("L2 无人值守总闸", body, meta="默认关 · 显式开启方生效")


def render_stats_fragment(payload: dict) -> str:
    """面板⑥：KPI 摘要行 + 双卡等宽两栏（任务画像·预算 | 管线质量·T+1 复核）。

    0.0.28 卡片化两栏；0.0.24：核心指标 .stats KPI 行置顶；kind 分布 chip 化；
    预算日桶近 14 日封顶（sparkline 仍全量）；§15/F-34 编号入 title。
    """
    d = payload.get("decisions") or {}
    chips = "".join(
        f'<span class="kchip"><i>{_esc(k)}</i> <b>{v}</b></span>'
        for k, v in (d.get("kinds") or {}).items()
    )
    rate = d.get("success_rate")
    # §⑨ 比率 2 位小数（0.0.34：此前 .0% 与同 KPI 条内 .2% 并存）
    rate_text = f"{rate:.2%}" if rate is not None else "—"
    summary = (
        f"done {d.get('done', 0)} · failed {d.get('failed', 0)} · "
        f"abandoned {d.get('abandoned', 0)} · 成功率 {rate_text}"
    )
    if d.get("note"):
        summary += f"（{_esc(d.get('note'))}）"
    route = payload.get("route_hit") or {}
    route_rate = route.get("rate")
    route_kpi = f"{route_rate:.2%}" if route.get("submitted") and route_rate is not None else "—"
    t1 = payload.get("t1_review") or {}
    t1_rate = t1.get("rate")
    t1_kpi = f"{t1_rate:.2%}" if t1 and t1_rate is not None else "—"
    done_n = int(d.get("done", 0))
    total_n = done_n + int(d.get("failed", 0)) + int(d.get("abandoned", 0))
    kpi_row = _stat_grid(
        (
            ("任务成功率", f"{rate_text} <small>{done_n}/{total_n}</small>", ""),
            ("意图路由命中率", route_kpi, ""),
            ("T+1 复核命中率", t1_kpi, ""),
        ),
        label="核心指标摘要",
    )
    left = [
        f'<p class="muted">{summary}</p>',
        f'<div class="chips">{chips}</div>' if chips else "",
        "<h3>预算曲线（token + 等效 USD 双口径）</h3>",
    ]
    curve = payload.get("budget_curve") or []
    if curve:
        shown = curve[-14:]
        crows = "".join(
            "<tr>"
            f'<td class="num">{_esc(p.get("date"))}</td><td class="num">{p.get("sessions", 0)}</td>'
            f'<td class="num">{p.get("tokens", 0)}</td>'
            f'<td class="num">{float(p.get("cost_usd_equiv") or 0):.2f}</td>'
            "</tr>"
            for p in shown
        )
        if curve:
            # 0.0.34：1 点也出 .svgs 容器——_sparkline_svg 自带「曲线待积累」空态分支
            left.append(
                '<div class="svgs">'
                '<span class="kchip" title="日会话 token 合计（近 14 日为表窗口，曲线全量）">'
                "<i>token</i></span>"
                + _sparkline_svg([p.get("tokens", 0) for p in curve], aria_label="token 日桶曲线")
                + '<span class="kchip" title="日等效 USD（F-34 双口径）"><i>等效 USD</i></span>'
                + _sparkline_svg(
                    [float(p.get("cost_usd_equiv") or 0) for p in curve],
                    aria_label="等效 USD 日桶曲线",
                )
                + "</div>"
            )
        cap_note = (
            '<p class="tbl-note">近 14 日（全量走势见上方曲线）</p>' if len(curve) > 14 else ""
        )
        left.append(
            '<div class="tbl-wrap sm">'
            + _table("w-ts|w-num|w-tok|w-usd", "日:num|会话:num|token:num|等效 USD:num", crows)
            + "</div>"
            + cap_note
        )
    else:
        left.append(_empty("暂无会话记账。"))
    right = []
    buckets = payload.get("assertion_buckets") or {}
    if buckets:
        brows = "".join(
            f'<tr><td>{_esc(k)}</td><td class="num">{v}</td></tr>' for k, v in buckets.items()
        )
        right.append(
            '<h3 title="NR-3 拒落盘原因（§15 指标族管线）">断言违例分桶</h3>'
            + _table("-|w-num", "违例类型|计数:num", brows)
            + '<p class="tbl-note">legacy 无码行归 uncategorized（0.0.22 前存量）</p>'
        )
    if route.get("submitted"):
        right.append(
            '<h3 title="确定性查表（§15 指标族管线）">意图路由命中率（确定性查表）</h3>'
            f'<p class="muted">提交 {route.get("submitted", 0)} · 兜底拒 '
            f"{route.get('fallback', 0)} · 命中率 {route_kpi}"
            f"（{_esc(route.get('note') or '')}）</p>"
        )
    cbk = payload.get("cost_by_kind") or []
    if cbk:
        crows = "".join(
            "<tr>"
            f'<td title="{_esc(r["kind"])}">{_esc(r["kind"])}</td>'
            f'<td class="num">{r["sessions"]}</td>'
            f'<td class="num">{r["tokens"]:,}</td>'
            f'<td class="num">{float(r["usd"]):.2f}</td>'
            "</tr>"
            for r in cbk
        )
        right.append(
            '<h3 title="按 kind 归因（§15 指标族管线）">会话成本分布（按 kind 归因）</h3>'
            + '<div class="tbl-wrap sm">'
            + _table("w-kind|w-num|w-tok|w-usd", "kind|会话:num|token:num|等效 USD:num", crows)
            + "</div>"
        )
    if t1:
        t1_note = f"（{_esc(t1.get('note'))}）" if t1.get("note") else ""
        right.append("<h3>T+1 复核命中率（advice 卡 vs ledger 投影）</h3>")
        right.append(
            f'<p class="muted" title="MIN_SAMPLES=30 · 样本 &lt;30 只观察不记分">命中 '
            f"{t1.get('hits', 0)} / 样本 {t1.get('sampled', 0)} · 命中率 {t1_kpi}{t1_note}</p>"
        )
    if not right:
        right.append(_empty("暂无管线质量样本。", "会话与断言记录积累后于此呈现。"))
    parts = [
        kpi_row,
        '<div class="grid-2"><!-- 双卡等宽两栏：任务画像·预算 | 管线质量·T+1 复核 -->',
        _card(
            "任务画像 · 预算双口径",
            "".join(left),
            meta="全样本登记 · 含失败与弃单|全样本登记，含失败与弃单",
        ),
        _card("管线质量 · T+1 复核", "".join(right), meta="指标族管线|§15 指标族管线"),
        "</div>",
        '<p class="muted">后验画像：全样本登记含亏损；模拟绩效均为假设性推演。</p>',
    ]
    return "".join(parts)


def render_briefing_fragment(payload: dict) -> str:
    """晨报/复盘卡区（0.0.18 分槽 + 行动项 chip 置顶）：盘前/盘后/legacy 三组 + ack 带槽位。"""
    day = payload.get("date", "")
    cards = payload.get("cards") or []
    key_levels = payload.get("key_levels") or {}
    from stock_trading_agent.server.panel_data import briefing_action_items

    action_items = briefing_action_items([str(c.get("md", "")) for c in cards])
    parts = [f"<h3>{day}</h3>"]
    if action_items:
        chips = "".join(
            f'<div class="kchip"><span class="code num">{_esc(i["code"])}</span>'
            f"<span>复核：{_esc(i['item'])}</span></div>"
            for i in action_items[:8]
        )
        parts.append(f'<div class="svgs">{chips}</div>')

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
        chip_html = f'<div class="chips">{chips}</div>' if chips else ""
        body = _esc(str(card.get("md", ""))[:1200])
        return (
            f'<div class="task-card"><p><strong>{code}</strong> {slot_tag} · {state} {action}</p>'
            f"{chip_html}"
            '<details class="brief-fold"><summary class="brief-sum">正文（节选 ≤1200 字）'
            f'</summary><pre class="md-pre">{body}</pre></details></div>'
        )

    if not cards:
        parts.append(
            '<p class="empty">当日无晨报/复盘卡。<span class="empty-tip">'
            "L2 开启时盘前 09:00–09:29 / 盘后 18:15 起自动触发；"
            "也可在②页手动触发 briefing_morning / briefing_evening。</span></p>"
        )
        return "".join(parts)
    for slot_key, title in (("am", "盘前晨报"), ("pm", "盘后复盘")):
        group = [c for c in cards if c.get("session") == slot_key]
        if group:
            parts.append(f'<h4 class="slot-h">{title}</h4>')
            parts.extend(_card_html(c) for c in group)
    legacy = [c for c in cards if not c.get("session")]
    if legacy:
        parts.append('<h4 class="slot-h">通用（legacy briefing_review）</h4>')
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
        '<p class="frm-tip" title="NR-3 断言器前置门">LLM 只产意见 · 聚合一律确定性裁决 · '
        "断言器前置门 · 锁/禁跑窗内提交将被拒（423）</p></form>"
        '<div id="dg-result" class="dg-result"></div>'
    )


def _ts_hhmm(ts: str) -> str:
    """①时间轴显示层：ISO 串转市场钟面取 HH:MM；非 ISO 短串原样（全值走 title Tooltip）。

    0.0.34：台账/落盘串多为 +00:00，纯切片会把 UTC 钟面当市场钟面（⑤页落盘 13:26 vs
    拒卡 21:26 双钟面并存）——aware 串 astimezone(MARKET_TZ)（市场时区 SSOT；与 routes
    拒卡列同源，系统本地 astimezone() 在非市场时区机上会错钟面，CI/UTC 实证）；朴素串
    视为已在市场钟面原样取位（监控事件/文件 stat 均为 aware 串，此分支仅兜底）。
    """
    import datetime as _dt

    from stock_trading_agent.loop.events import MARKET_TZ

    try:
        parsed = _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)[11:16] if len(str(ts)) > 16 else str(ts)
    if parsed.tzinfo is None:
        return parsed.strftime("%H:%M")
    return parsed.astimezone(MARKET_TZ).strftime("%H:%M")


def _ts_short(ts: str) -> str:
    """跨日台账显示层：ISO 串转本地时区 MM-DD HH:MM；解析失败回退原串（全值走 title）。

    sessions/decisions 等台账为 +00:00 串，纯切片会显示错钟点，必须 astimezone() 转本地。
    """
    import datetime as _dt

    try:
        return _dt.datetime.fromisoformat(str(ts)).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return str(ts)


def _lv_class(level: str) -> str:
    """事件梯 T 级配色（图 2-1：机械/刷新/需人确认分色；lv1/lv2 样式 index.html 已备）。"""
    return {"T0": "lv0", "T1": "lv1", "T2": "lv2"}.get(str(level or "T0"), "lv0")


def render_event_feed(payload: dict) -> str:
    """①今日事件流：时间轴形态（T 徽章/mech_risk riskchip/风险行高亮/现价与距线）。

    0.0.24：整行包 <a href="#/monitor">（.ev-link）——①流条目可跳③页事件表与复核。
    """
    events = (payload.get("events") or [])[-10:]
    if not events:
        return (
            _empty(
                "今日暂无盯盘事件。",
                "开盘时段自动盯盘；如持续为空，检查①页右列系统状态卡或③页事件表。",
                title="watch.py 台账为空或未运行",
            )
            + '<span id="ov-ev-count" hx-swap-oob="true" class="cm">0 条</span>'
        )
    rows = []
    names = payload.get("names") or {}
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
        code = str(ev.get("code", ""))
        name_tail = f" {_esc(names.get(code, ''))}" if names.get(code) else ""
        rows.append(
            f'<a class="ev-link" href="#/monitor" title="在③页查看事件表与复核">'
            f'<div class="ev{risk_cls}"><div class="ev-time num" title="{_esc(ts)}">'
            f"{_esc(_ts_hhmm(ts))}</div>"
            f'<div class="ev-rail"><div class="ev-dot"></div></div>'
            f'<div class="ev-name"><span class="ev-stock"><span class="code num">'
            f"{_esc(code)}</span>{name_tail} · {_esc(ev.get('rule', ''))}</span>"
            f'<span class="ev-top">{_esc(price_txt)} {riskchip}'
            f'<span class="lv {_lv_class(ev.get("level", "T0"))}"'
            f">{_esc(ev.get('level', 'T0'))}</span>"
            f"{_esc(review_txt)}</span></div></div></a>"
        )
    oob = f'<span id="ov-ev-count" hx-swap-oob="true" class="cm">{len(events)} 条</span>'
    return "".join(rows) + oob


def render_calendar(payload: dict) -> str:
    """①事件日历：日期升序 kchip 条目（披露/解禁/公告）。"""
    rows = payload.get("upcoming") or []
    if not rows:
        return _empty(
            "暂无日历事件。",
            "诊股产出证据后自动聚合披露/解禁/公告；A 股专属事件不适用于港股。",
            title="需 T1 刷新或诊股会话产出 evidence",
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
    """③通知预览：已发通知历史重放（0.0.24 同源聚合折叠 + 时间戳区间）。

    按 (source, code / title+body 前缀) 分组：每组一卡——×N 聚合 chip、首/末时刻区间、
    首条正文摘要 + details 展开逐条；时间戳显示 HH:MM（title 挂全值），「·现在」删除。
    """
    rows = payload.get("items") or []
    if not rows:
        cards = _empty(
            "暂无已发通知。",
            "盯盘触发本地通知后于此只读重放。",
            title="watch.py osascript 历史；产品仓永不发通知",
        )
        status_line = ""
        # 0.0.34：空态发射归零 oob——日志轮转/清空后卡头 chip 复位，不残留陈旧计数
        meta_oob = (
            '<span class="cm" id="mo-notif-meta" hx-swap-oob="true" role="status" '
            'aria-live="polite" title="osascript 通道 · 只读">osascript 通道 · 只读</span>'
        )
    else:
        groups: list[list[dict]] = []
        index: dict[tuple[str, str], int] = {}
        for row in rows:
            code = str(row.get("code") or "")
            key = (
                str(row.get("source") or ""),
                code if code else f"{row.get('title', '')}|{row.get('body', '')[:24]}",
            )
            if key in index:
                groups[index[key]].append(row)
            else:
                index[key] = len(groups)
                groups.append([row])
        cards = "".join(_notification_group_html(group) for group in groups)
        status_line = ""
        # 0.0.34：计数条升卡头 meta（oob 更新）——原置 520px 滚动容器底部首屏不可见；
        # class="cm" 必带：oob 为 outerHTML 替换，响应元素属性即终态（丢失即破卡头样式）
        meta_oob = (
            f'<span class="cm" id="mo-notif-meta" hx-swap-oob="true" '
            f'role="status" aria-live="polite" '
            f'title="osascript 通道 · 只读">重放 {len(groups)} 组 · 共 {len(rows)} 条</span>'
        )
    return (
        meta_oob
        + f'<div role="region" aria-label="通知预览（历史重放）">{cards}{status_line}</div>'
        '<p class="tbl-note" title="仅本地 osascript（terminal 通道）· 禁第三方推送（红线 7）· '
        "告警主通道永久留服务端（后台标签页冻结，I-48）· 锁屏不保证送达——离机触达按面板"
        '常驻拉取式主动查看设计（方案 §1）——本卡为只读历史重放">'
        "仅本地通道 · 禁第三方推送 · 告警主通道留服务端 · 本卡为只读历史重放</p>"
    )


def _notification_group_html(group: list[dict]) -> str:
    """同源通知组一卡：首/末时刻 + ×N + 首条摘要 + details 逐条（tail 内全量可查）。"""
    first, last = group[0], group[-1]

    def _time_span(row: dict) -> str:
        ts = str(row.get("ts", ""))
        return f'<span class="num note-time" title="{_esc(ts)}">{_esc(_ts_hhmm(ts))}</span>'

    badge = f' <span class="chip chip-warn">×{len(group)}</span>' if len(group) > 1 else ""
    expand = ""
    if len(group) > 1:
        items = "".join(f"<div>{_time_span(r)} {_esc(r.get('body', ''))}</div>" for r in group)
        expand = (
            f'<details class="brief-fold"><summary class="brief-sum">全部 '
            f'{len(group)} 条</summary><div class="note-text">{items}</div></details>'
        )
    span_html = f"{_time_span(first)} → {_time_span(last)}" if len(group) > 1 else _time_span(first)
    return (
        '<div class="card note"><div class="card-b note-b">'
        '<svg class="note-ico" width="16" height="16" viewBox="0 0 16 16">'
        '<path d="M8 1a4 4 0 0 0-4 4v3l-1.5 2.5h11L12 8V5a4 4 0 0 0-4-4z'
        'M6.5 12.5a1.5 1.5 0 0 0 3 0" '
        'stroke-width="1.3" fill="none"/></svg>'
        '<div class="note-main">'
        f"{span_html}{badge}"
        '<div class="note-title">'
        f"{_esc(first.get('title', ''))}</div>"
        '<div class="note-text">'
        f"{_esc(first.get('body', ''))}</div>{expand}</div></div></div>"
    )


def render_session_obs(body: dict) -> str:
    """②会话观测：五段进度条（done/cur/pend）+ 双轨意见板 + 裁决卡（P2 §⑫ 案 a）。"""
    stages = body.get("stages") or []
    if not stages:
        return _empty("无会话产物视图。")
    steps = []
    for st in stages:
        cls = {"done": "done", "current": "cur", "pending": "pend"}.get(st.get("state"), "pend")
        steps.append(
            f'<div class="step {cls}"><div class="sc">{int(st["stage"])}</div>'
            f'<div class="sl">{_esc(st["name"])}</div>'
            f'<div class="ss num">{_esc(st.get("note", ""))}</div></div>'
        )
    parts = ['<div class="pipe pipe-dense">']
    parts.append("".join(steps))
    parts.append("</div>")
    opinions = body.get("opinions") or {}
    default_cards = opinions.get("default") or []
    if default_cards:
        cards = "".join(
            f'<div class="op"><div class="op-src num">{_esc(c["source"])}</div>'
            f'<div><span class="sig {_SIG_CLASS.get(c.get("signal"), "")}">'
            f'<span class="d"></span>{_esc(c.get("signal") or "")}</span> '
            f'<span class="num">{_esc(c.get("confidence"))}</span></div></div>'
            for c in default_cards[:16]
        )
        position_n = len(opinions.get("position") or [])
        pos_note = f"（中线轨 {position_n} 条另裁决）" if position_n else ""
        parts.append(f'<h3 class="h3-tight">双轨意见板 · {len(default_cards)} 条{pos_note}</h3>')
        parts.append(f'<div class="opins opins-dense">{cards}</div>')
    else:
        parts.append(_empty("意见未产出。", "LLM 意见段待会话推进。"))
    verdict = body.get("verdict")
    if verdict:
        buckets = verdict.get("buckets") or {}
        bucket_line = " · ".join(f"{_esc(k)} {len(v)}" for k, v in buckets.items())
        rg_log = "；".join(verdict.get("risk_guard_log") or []) or "no-op"
        parts.append('<div class="verd verd-box">')
        parts.append(
            '<div class="num verd-line">'
            f"initial {_esc(verdict.get('initial'))} → final "
            f'<b class="verd-final">{_esc(verdict.get("final"))}</b>'
            f" · 置信 {_esc(verdict.get('confidence'))} · "
            f"{_esc(verdict.get('consensus_level'))}</div>"
        )
        parts.append(f'<div class="verd-sub">RiskGuard：{_esc(rg_log)}</div>')
        parts.append(f'<div class="verd-sub">分歧：{_esc(bucket_line) or "无"}</div>')
        parts.append("</div>")
        parts.append(
            '<p class="tbl-note">LLM 零直写裁决面（R3）· RiskGuard 只降不升（R5）'
            "· 裁决链经 adjudicate 原命令</p>"
        )
    notes = body.get("notes") or []
    if notes:
        parts.append(f'<p class="muted">{_esc(notes[0])}</p>')
    return "".join(parts)


def render_signals_table(payload: dict) -> str:
    """①在册信号卡表（信号五词/置信/距止损/轨；0.0.24 breach 置顶 + 表格规范补齐）。

    距止损全表无现价源时条件省列（colgroup/thead/td 三处联动）；
    置信数值统一两位小数；排序由 panel_data.signals_snapshot 保证（breach→near→常序）。
    """
    rows = payload.get("rows") or []
    if not rows:
        tip = (
            "signals.json 不可读——宁拒不漏，显示为空而非旧数据。"
            if not payload.get("read_ok")
            else "adjudicate 产出登记后于此呈现。"
        )
        return _empty("无在册信号。", tip)
    names = payload.get("names") or {}
    has_stop = any(r.get("to_stop_pct") is not None for r in rows)
    trs = []
    for row in rows:
        signal = row.get("signal")
        sig_cls = _SIG_CLASS.get(signal, "")
        to_stop = row.get("to_stop_pct")
        dist_cls = (
            "dist-breach"
            if to_stop is not None and to_stop <= 0
            else ("dist-near" if to_stop is not None and to_stop <= 3 else "")
        )
        stop_td = f'<td class="num {_esc(dist_cls)}">{_fmt(to_stop, "%")}</td>' if has_stop else ""
        conf = row.get("confidence")
        conf_txt = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else _fmt(conf)
        name = names.get(row["code"])
        stock_tip = _esc(f"{row['code']} {name}" if name else row["code"])
        trs.append(
            "<tr>"
            f'<td title="{stock_tip}"><span class="code num">{_esc(row["code"])}</span>'
            f"{_name_tail(names, row['code'])}</td>"
            f'<td><span class="sig {sig_cls}"><span class="d"></span>'
            f"{_esc(signal or row.get('signal_raw'))}</span></td>"
            f'<td class="num">{conf_txt}</td>'
            f"{stop_td}"
            f"<td>{_esc('中线' if row.get('profile') == 'position' else '短线')}</td>"
            f'<td class="num">{_esc(row.get("date", ""))}</td>'
            "</tr>"
        )
    stop_col = "w-num|" if has_stop else ""
    stop_head = "距止损:num|" if has_stop else ""
    return (
        '<div class="tbl-wrap">'
        + _table(
            f"w-stock|-|w-num|{stop_col}w-st|w-ts",
            f"代码|最新信号|置信:num|{stop_head}轨|信号日:num",
            "".join(trs),
        )
        + "</div>"
        + '<p class="tbl-note" title="adjudicate 产出 · 盘中结论以收盘复核为准（R9）；'
        "距止损需现价源（paper last_price / 盘中 facts.price），无价显 —；"
        '排序 breach 置顶">adjudicate 产出 · 盘中结论以收盘复核为准 · '
        "距止损需现价源（无价显 —）</p>"
    )


_TODO_HREF = {
    "pending_fill": "#/paper",
    "mech_risk": "#/monitor",
    "briefing": "#/overview",
}


def render_todos(payload: dict) -> str:
    """①今日待办卡：三源聚合（pending 优先）；行级 href 按 source 跳对应页（0.0.24）。

    假复选框 .cbx 已换只读圆点（三源派生只读投影，不支持勾选——§11 只读半边）。
    """
    todos = payload.get("todos") or []
    if not todos:
        return (
            _empty("无待办。", "逐笔确认/降档复核/晨报确认产生时于此汇总。")
            + '<span id="ov-todo-count" hx-swap-oob="true" class="cm">0 项</span>'
        )
    rows = []
    for item in todos:
        href = _TODO_HREF.get(str(item.get("source", "")), "#/overview")
        rows.append(
            f'<div class="todo"><span class="sdot sdot-wait" '
            'title="待办 · 三源派生只读投影，不支持勾选"></span>'
            f'<div class="td-tx"><a class="todo-link" href="{_esc(href)}">'
            f'<span class="code num">{_esc(item["code"])}</span> {_esc(item["text"])}</a></div>'
            f'<span class="chip chip-warn">{_esc(item["chip"])}</span></div>'
        )
    return "".join(rows) + (
        f'<span id="ov-todo-count" hx-swap-oob="true" class="cm">{len(todos)} 项</span>'
    )


def render_navcard(view: dict) -> str:
    """①模拟盘净值卡：NAV 大数字 + 今日%/成立以来% + sparkline。

    0.0.24 修单数据点结构断裂：今日 chip 条件追加、大数字与 chips 容器恒输出。
    """
    history = view.get("equity_history") or []
    if not history:
        return _empty("净值曲线待积累。", "盘后日终复核产出权益点后于此呈现。")
    navs = [float(p.get("nav") or 0) for p in history]
    nav = navs[-1]
    today_pct = ((nav / navs[-2] - 1) * 100) if len(navs) >= 2 and navs[-2] else None
    since_pct = (nav - 1.0) * 100
    chips = []
    if today_pct is not None:
        chip_cls = "chip-up" if today_pct >= 0 else "chip-neutral"
        chips.append(f'<span class="chip {chip_cls}">今日 {today_pct:+.2f}%</span>')
    chips.append(f'<span class="chip chip-neutral">成立以来 {since_pct:+.2f}%</span>')
    return (
        f'<div class="nv-v num">{nav:.4f}</div>'
        f'<div class="nv-chips">{"".join(chips)}</div>'
        + _sparkline_svg(navs, aria_label=f"模拟盘净值走势，最新 NAV {nav:.4f}", fluid=True)
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
            f'<div class="sys-l"><span class="sys-name">{name}</span>'
            f'<div class="sys-desc" title="{_esc(desc)}">{desc}</div></div>'
            f'<span class="sys-sched num">{sched[name]}</span>'
            f'<span class="sys-st">{state}</span></div>'
        )
    cc_active = bool((status.get("loop") or {}).get("active"))
    probe_at = (l2_state or {}).get("updated_at") or ""
    probe_html = (
        f'<span class="sys-sched num" title="probe 认证态判定时刻">probe {probe_at[:19]}</span>'
        if probe_at
        else "<span></span>"  # 轨道占位：sys-row 4 轨对 4 子元素（§⑪ 网格子元素=轨道数）
    )
    cc_desc = "headless 子进程唯一 LLM 通道"
    rows.append(
        f'<div class="sys-row"><div class="sdot {"sdot-ok" if cc_active else "sdot-wait"}"></div>'
        '<div class="sys-l"><span class="sys-name">CC 大脑</span>'
        f'<div class="sys-desc" title="{_esc(cc_desc)}">{_esc(cc_desc)}</div></div>'
        f"{probe_html}"
        f'<span class="sys-st">{"运行中" if cc_active else "降级只读"}</span></div>'
    )
    chain_error = (status.get("loop") or {}).get("chain_error")
    if chain_error:
        rows.append(
            '<div class="sys-row"><div class="sdot sdot-wait"></div>'
            '<div class="sys-l"><span class="sys-name">决策链</span>'
            f'<div class="sys-desc" title="{_esc(str(chain_error))}">'
            f"{_esc(str(chain_error))}</div></div>"
            "<span></span>"
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
    """②报告档案列表（目录+latest+count，点击查正文；show:top 滚动定位）。"""
    rows = payload.get("reports") or []
    if not rows:
        return _empty(
            "暂无诊股报告。",
            "在上方表单发起诊股，报告段落完成后于此归档。",
            title="提交诊股后由 vibe-stock report 段产出",
        )
    trs = []
    for row in rows[:20]:
        link = (
            f'<a href="#" hx-get="/api/panel/report/{_esc(row["dir"])}/{_esc(row["latest"])}" '
            'hx-target="#dg-report-body" hx-swap="innerHTML show:top">'
            f'<span class="code num">{_esc(row["code"])}</span>.{_esc(row["market"])}</a>'
        )
        trs.append(
            f"<tr><td>{link}</td>"
            f'<td class="num" title="{_esc(row.get("latest", ""))}">{_esc(row.get("latest", ""))}'
            f"（{row.get('count', 0)} 份在档）</td></tr>"
        )
    return (
        '<div class="tbl-wrap">'
        + _table("w-code|w-file", "标的|最新报告:num", "".join(trs))
        + "</div>"
        + '<p class="tbl-note" title="vibe-stock/04 规约结构 · 免责段必含">'
        "报告按规约结构归档 · 免责段必含</p>"
    )


def _md_lite(md: str) -> str:
    """报告 md 极简结构化渲染（状态机 ~70 行；支持 #/##/###/####、| 表格、``` 围栏、
    - 列表、**粗体**；全部文本先 _esc 再包标签——零依赖零前端库，无构建链约束内）。"""
    out: list[str] = []
    in_code = False
    in_table = False
    in_list = False
    table_rows: list[list[str]] = []

    def close_table() -> None:
        nonlocal in_table, table_rows
        if in_table:
            # 首行升 thead（报告 md 均含表头行；动态列数豁免——colgroup 不带 .w-*）
            head = table_rows[0] if table_rows else []
            body_rows = "".join(
                "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in table_rows[1:]
            )
            out.append(_table("|".join("-" * len(head)), "|".join(head), body_rows))
            in_table = False
            table_rows = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            close_table()
            close_list()
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(_esc(raw))
            continue
        if not line.strip():
            close_table()
            close_list()
            continue
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= {"-", ":", " "}:
                continue  # 分隔行
            if not in_table:
                close_list()
                in_table = True
                table_rows = []
            table_rows.append([_esc(c) for c in cells])
            continue
        close_table()
        if line.startswith("####"):
            close_list()
            out.append(f'<h4 class="md">{_esc(line.lstrip("#").strip())}</h4>')
        elif line.startswith("###"):
            close_list()
            out.append(f'<h3 class="md">{_esc(line.lstrip("#").strip())}</h3>')
        elif line.startswith("##"):
            close_list()
            out.append(f'<h2 class="md">{_esc(line.lstrip("#").strip())}</h2>')
        elif line.startswith("#"):
            close_list()
            out.append(f'<h1 class="md">{_esc(line.lstrip("#").strip())}</h1>')
        elif line.lstrip().startswith(("-", "* ")) and not line.lstrip().startswith("---"):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = line.lstrip()[1:].strip()
            out.append(f"<li>{_esc(item)}</li>")
        else:
            close_list()
            text = _esc(line)
            text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
            out.append(f'<p class="md">{text}</p>')
    close_table()
    close_list()
    if in_code:
        out.append("</pre>")
    return "".join(out)


def render_report_body(code_market: str, filename: str, md: str) -> str:
    has_disclaimer = "不构成投资建议" in md
    warn = "" if has_disclaimer else '<p class="riskchip">免责段缺失（异常）</p>'
    return _card(
        f"报告 · {_esc(code_market)} · {_esc(filename)}",
        f'{warn}<div class="md">{_md_lite(md)}</div>',
        meta="md 结构化渲染",
    )


def render_advice_detail(body: dict, *, mount: str = "ad") -> str:
    """③⑤建议卡详情：四块布局（候选动作/差异/份额原值 .bignum/现价并入的走势参考块）+ 复核底条。

    mount=mo→③页 #advice-detail / ad→⑤页 #advice-detail-2（按钮 target 按挂点映射）。
    """
    meta = body.get("meta") or {}
    rendered = body.get("rendered")
    reviewed = bool(body.get("reviewed"))
    price_fact = body.get("price") or {}
    from stock_trading_agent.loop.advice import parse_trend_table

    band_rows = parse_trend_table(body.get("md", ""))
    prev = meta.get("prev_final_signal", "none")
    final = meta.get("final_signal", "")
    date = body.get("date", "")
    code = meta.get("code", body.get("code", ""))
    target = "#advice-detail" if mount == "mo" else "#advice-detail-2"
    shares = (
        f'<div class="bignum num">{rendered["t1_sell_shares"]:,}</div>'
        f'<p class="tbl-note">持仓 {rendered["held_lots"]} 手 · 冻结后可卖 '
        f"{rendered['t1_sell_lots']} 手 × {rendered['board_lot']}/手（T+1，asof "
        f"{_esc(rendered['asof'])}）——呈现态原值"
        f'<span title="NR-5/R2 豁免面，落盘为百分比">（豁免面）</span></p>'
        if rendered
        else _empty("无模拟持仓。", "原值不可现算；落盘口径见卡面百分比。")
    )
    p = price_fact.get("price")
    if isinstance(p, (int, float)):
        asof = str(price_fact.get("asof") or "")
        # §⑬ 当日视图单钟面：asof 感知串转市场钟面（台账 asof 多为 +00:00，切片会显 UTC）；
        # 短串/日期串（≤16 字符）无时间位，维持原样展示
        asof_hhmm = _ts_hhmm(asof) if len(asof) > 16 else asof[:16]
        asof_txt = f" · asof {_esc(asof_hhmm)}" if asof else ""
        stale = ' <span class="tag">陈旧</span>' if price_fact.get("stale") else ""
        price_html = (
            f'<div class="num">{p}</div><p class="tbl-note" '
            'title="多源合并取 asof 最新（§⑫ 现价源）">'
            f"源 {_esc(price_fact.get('source', ''))}"
            f"{_esc(asof_txt)}{stale}——多源合并取 asof 最新</p>"
        )
    else:
        price_html = _empty(
            "—",
            "三源现价缺席，σ√h 参考带停用。",
            title="三源现价缺席（paper last_price / 盘中 facts / T1 日 K 收盘）",
        )
    trend_rows = "".join(
        f"<tr><td>{_esc(r['horizon'])}</td>"
        f'<td class="num" title="带下沿 {_esc(r["low"])}">{r["low"]}</td>'
        f'<td class="num" title="带上沿 {_esc(r["high"])}">{r["high"]}</td>'
        f'<td class="num" title="触及带下沿概率 {_esc(r["prob_touch_low_pct"])}%">'
        f"{r['prob_touch_low_pct']}%</td></tr>"
        for r in band_rows
    )
    trend = (
        _table("-|w-num|w-num|w-num", "h|带下沿:num|带上沿:num|P 触下:num", trend_rows)
        if band_rows
        else _empty("无走势参考表。")
    )
    reviewed_html = (
        '<span class="st done">已复核</span>'
        if reviewed
        else (
            f'<button class="mini" hx-post="/api/panel/advice/review" '
            f'hx-vals=\'{{"date": "{_esc(date)}", "code": "{_esc(code)}", '
            f'"mount": "{_esc(mount)}"}}\' '
            f'hx-target="{target}" hx-swap="innerHTML" '
            f'hx-disabled-elt="this">标记已复核</button>'
        )
    )
    return (
        f'<div class="card adv"><div class="card-h">'
        f'<span class="lv lv2" title="monitor-advice/1">T2 卡</span>'
        '<span class="ct">T2 建议卡 · '
        f'<span class="code num">{_esc(code)}</span></span>'
        '<span class="cm">已重裁决 ✓ NR-1 · '
        f"{'已复核 ✓' if reviewed else '待用户复核'}</span></div>"
        '<div class="card-b"><div class="adv-grid">'
        '<div class="adv-blk"><div class="adv-k" title="vibe-stock/04 五档分类学措辞">'
        f"候选动作（五档措辞）</div>"
        f'<div class="quote">「{_esc(meta.get("candidate_action", ""))}」</div></div>'
        f'<div class="adv-blk"><div class="adv-k">与上一裁决差异（adjudicate 重裁决产出）</div>'
        f'<div class="num">{_esc(prev)} → {_esc(final)}</div></div>'
        f'<div class="adv-blk"><div class="adv-k">T+1 可卖份额（呈现态）</div>{shares}</div>'
        '<div class="adv-blk"><div class="adv-k">现价 · 走势参考（σ√h · 零权重）</div>'
        f"{price_html}{trend}</div>"
        "</div>"
        '<p class="tbl-note">盘中结论以收盘复核为准 · 候选动作为五档分类学措辞 · 不构成投资建议</p>'
        '<div class="adv-foot">'
        '<span class="warn-tx">待收盘复核 · 非投资建议 · AI 生成标注 · 用户复核前置</span>'
        f'<span class="fx"><a class="mini" href="/api/advice/export/{_esc(date)}/{_esc(code)}" '
        f"download>导出脱敏卡片</a>{reviewed_html}</span></div>"
        "</div></div>"
    )


def render_paper_fragment(payload: dict, *, notice: str = "") -> str:
    """面板④（0.0.31 一屏化三签）：账户持仓（KPI 四格 + 持仓表/空态）|
    逐笔与曲线 [逐笔确认队列 1fr | 权益曲线 + 模拟绩效 rail 500px] | 公司行动登记。

    NR-5 呈现态豁免（13-paper §⑥）：面板可显份额原值（导出/落盘卡仍降级脱敏）。
    """
    notice_html = f'<p class="ok">{_esc(notice)}</p>' if notice else ""
    names = payload.get("names") or {}
    positions = payload.get("positions") or []
    totals = payload.get("totals") or {}
    exists = bool(payload.get("exists"))

    # ── 卡A 账户与持仓（KPI 四格恒置顶；账未建时四格统一 —（§⑨ 缺数非零））──
    def _kpi_v(key: str, *, money: bool = False) -> str:
        # money：千分位 + 两位小数（§⑨；与同卡 tbl-note 口径一致，0.0.34 前裸 float）
        if not exists:
            return "—"
        v = totals.get(key)
        if money and v is not None:
            return f"{float(v):,.2f}"
        return _fmt(v)

    kpi = _stat_grid(
        (
            ("现金", _kpi_v("cash", money=True), ""),
            ("总市值", _kpi_v("market_value", money=True), ""),
            ("总资产", _kpi_v("total", money=True), ""),
            ("净值", _kpi_v("nav"), ""),
        ),
        label="模拟盘账户摘要",
        cls="stats-4",
    )
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
            rows.append(
                f"<tr{open_cls}>"
                f'<td><span class="code">{_esc(pos["code"])}</span>'
                f"{_name_tail(names, pos['code'])}</td>"
                f"<td>{_pos_status_pill(pos['status'])}</td>"
                f"<td>{badges}</td>"
                f'<td class="num">{pos.get("lots", 0)}</td>'
                f'<td class="num">{pos.get("sellable_lots", 0)}</td>'
                f'<td class="num">{_fmt(pos.get("avg_cost"))}</td>'
                f'<td class="num">{_fmt(pos.get("last_price"))}</td>'
                f'<td class="num">{pnl_html}</td>'
                f"{_money_td(pos.get('market_value'))}"
                "</tr>"
            )
        pos_body = (
            # 0.0.34：股票列对齐 ①③ w-stock 先例、市值走 w-amt 105（千分位金额，1440 档
            # 列随表展宽全显）、可卖头缩短为「可卖T+1」；列宽和 838→807 恰 ≤ 880 档卡内宽
            # 807（§⑬ 列宽和规则）——建仓批 w-batch 84 + 徽章格解 nowrap 竖排（单枚 72px
            # 恰容），市值超宽值 ellipsis+title 全值兜底；
            # 外裹 tbl-wrap 令定宽恒被尊重（断点不再比例挤压）
            '<div class="tbl-wrap">'
            + _table(
                "w-stock|w-st|w-batch|w-n2|w-n2|w-num|w-num|w-num|w-amt",
                "股票|状态|建仓批|手数:num|可卖T+1:num|成本价:num|现价:num|盈亏%:num|市值:num",
                "".join(rows),
            )
            + "</div>"
        ) + (
            f'<p class="tbl-note">总市值 {totals.get("market_value", 0):,.2f} · '
            f"现金 {totals.get('cash', 0):,.2f} · 总资产 {totals.get('total', 0):,.2f} · "
            f"净值 {totals.get('nav', 0):.4f}（盈亏口径：红涨绿跌）</p>"
        )
    else:
        pos_body = (
            _empty(
                "无模拟持仓。",
                'T2 触价建议经④页逐笔确认后建仓——<a href="#/advice">先去⑤页看建议卡</a>。',
            )
            if exists
            else _empty(
                "无模拟持仓。",
                "首笔确认成交后自动建账（T2 触价 → 逐笔确认队列）。",
                title="账本未建或全部清仓",
            )
        )
    tab_account = _card(
        "账户与持仓",
        f"{kpi}{pos_body}",
        meta="严格穿价撮合 · 含费用与滑点|严格穿价撮合 · 静态费用栈",
    )

    # ── 两栏行：逐笔确认队列（主）| 权益曲线 + 模拟绩效（rail）──
    pending = payload.get("pending") or []
    if pending:
        rows = []
        for req in pending:
            if req.get("state") != "pending":
                continue
            rid = _esc(req.get("request_id", ""))
            confirm_btn = (
                f'<button class="mini" hx-post="/api/panel/paper/confirm" '
                f'hx-vals=\'{{"request_id": "{rid}"}}\' hx-target="#paper" '
                'hx-swap="innerHTML" hx-disabled-elt="this" '
                "hx-confirm='确认成交入账？该笔将写入台账与决策链，不可撤销"
                "（模拟盘 NR-2：永不接真实下单）。'>确认成交</button>"
            )
            reject_btn = (
                f'<button class="mini" hx-post="/api/panel/paper/reject" '
                f'hx-vals=\'{{"request_id": "{rid}"}}\' hx-target="#paper" '
                'hx-swap="innerHTML" hx-disabled-elt="this" '
                "hx-confirm='驳回该笔待确认成交？将从队列移除，不可恢复。'>驳回</button>"
            )
            ts = str(req.get("ts", ""))
            rows.append(
                "<tr>"
                f'<td class="num" title="{_esc(ts)}">{_esc(_ts_short(ts))}</td>'
                f'<td><span class="code">{_esc(req.get("code", ""))}</span></td>'
                f"<td>{_esc(req.get('tranche_leg', ''))}</td>"
                f"<td>{_esc(req.get('side', ''))}</td>"
                f'<td class="num">{_esc(req.get("lots"))}</td>'
                f'<td class="num">{_esc(req.get("ref_price"))}</td>'
                f'<td class="num">{_esc(req.get("bar_date", ""))}</td>'
                f'<td><span class="opgrp">{confirm_btn}{reject_btn}</span></td>'
                "</tr>"
            )
        queue_body = _table(
            # 0.0.34：列宽和 876→762（批→84/方向与手数→60 窄轨），≤1180 单栏卡内宽不再溢出；
            # 时间为台账制 _ts_short（MM-DD HH:MM）保 w-ts 全格式轨
            "w-ts|w-code|w-num|w-n2|w-n2|w-num|w-ts|w-op",
            "时间:num|代码|批|方向|手数:num|参考价:num|bar 日:num|操作",
            "".join(rows),
        )
    else:
        queue_body = _empty("队列为空。", "触价建议产生后于此人工确认。")

    history = payload.get("equity_history") or []
    perf = payload.get("perf")
    nr2_note = (
        '<p class="nr2-mini"><b>NR-2</b>：模拟盘永不接真实下单接口；'
        "全部模拟绩效为假设性推演（含费用与滑点）。</p>"
    )
    if perf is not None:  # 复用 0.0.15 _perf_grid（单一渲染件，勿并存两版绩效三格）
        note = perf.get("note") or "样本 ≥30 可记分"
        perf_body = (
            _perf_grid(perf) + f'<p class="stat-note">假设性推演 · 含费用与滑点 · 平仓 '
            f"{int(perf.get('closed_pairs', 0))} 笔 · {_esc(note)}</p>{nr2_note}"
        )
    else:
        perf_body = (
            _empty(
                "暂无可记分绩效样本。",
                "平仓对积累后自动记分（样本 ≥30）。",
                title="perf 样本不足时不记分",
            )
            + nr2_note
        )
    tab_flow = (
        '<div class="grid-rail flex-fill"><div class="col">'
        + _card(
            "逐笔确认队列",
            queue_body,
            meta="触价只写待确认 · 无人工确认不落账|触价只写待确认；无人工确认不落账",
        )
        + '</div><div class="col">'
        + _card(
            "权益曲线",
            f'<div class="eq-svg">{_axis_equity_svg(history)}</div>',
            meta="NAV = 权益 / 预算",
        )
        + _card(
            "模拟绩效",
            perf_body,
            meta="假设性推演 · 样本 ≥30 可记分|假设性推演（含费用与滑点）；样本 ≥30 可记分",
        )
        + "</div></div>"
    )

    # ── 公司行动登记全宽卡（馈送表单在 index.html #paper-ca 独立卡）──
    records = payload.get("corporate_actions") or []
    if records:
        rows = "".join(
            "<tr>"
            f'<td><span class="code">{_esc(r.get("code", ""))}</span></td>'
            f"<td>{'送转' if r.get('kind') == 'split' else '分红'}</td>"
            f'<td class="num">{_esc(r.get("ex_date", ""))}</td>'
            f'<td class="num" title="{_esc(r.get("note", ""))}">{_esc(_ca_basis(r))}</td>'
            f'<td class="num">{_fmt(r.get("cash_delta"))}</td>'
            "</tr>"
            for r in records[-10:]
        )
        ca_body = _table(
            "w-code|w-kind|w-ts|-|w-num", "代码|类型|除权日:num|口径:num|现金变动:num", rows
        )
    else:
        ca_body = _empty("暂无公司行动登记。", "馈送入口在下方「公司行动馈送」卡（需在持仓）。")
    tab_ca = _card(
        "公司行动登记",
        ca_body,
        meta="先送转后分红 · 不产 Operation 行|先送转后分红；不产 Operation 行",
    )
    # 0.0.31 一屏化：三签（账户持仓 | 逐笔与曲线 | 公司行动），radio 零 JS、htmx 契约不动
    return (
        notice_html + '<div class="subtabs flex-fill">'
        '<div class="st-labels">'
        '<input type="radio" name="pp-st" id="pp-st-1" checked>'
        '<label for="pp-st-1">账户持仓</label>'
        '<input type="radio" name="pp-st" id="pp-st-2"><label for="pp-st-2">逐笔与曲线</label>'
        '<input type="radio" name="pp-st" id="pp-st-3"><label for="pp-st-3">公司行动</label>'
        "</div>"
        '<div class="subpanels">'
        f'<div class="subpanel">{tab_account}</div>'
        f'<div class="subpanel">{tab_flow}</div>'
        f'<div class="subpanel">{tab_ca}</div>'
        "</div></div>"
    )


def render_ca_form(*, has_positions: bool = True) -> str:
    """④公司行动手动馈送表单（0.0.24 外置 load-only 容器：轮询重渲不再擦输入态）。

    提交落点 #paper-ca-result（表单自带）；成功窄通知 + SSE paper 事件驱动 #paper 重拉；
    无持仓时 details 收起（提交必 422）。
    """
    form = (
        '<form class="frm" hx-post="/api/panel/paper/corporate-action" '
        'hx-target="#paper-ca-result" hx-swap="innerHTML" hx-disabled-elt="find button" '
        "hx-confirm='馈送公司行动入账（改写持仓/现金，不可撤销）？'>"
        '<div class="frm-row">'
        '<label for="ca-kind" title="送转=股数重算（10送5 填 15/10）；分红=每股税前现金'
        '（FIFO 分档红利税自动计）。同日除权除息请先提交送转、再提交分红">类型</label>'
        '<select class="sel" id="ca-kind" name="kind">'
        '<option value="split">送转（split）</option>'
        '<option value="dividend">分红（dividend）</option>'
        "</select></div>"
        '<div class="frm-row">'
        '<label for="ca-code" title="须为在持仓位代码（无持仓 422）">代码</label>'
        '<input class="inp num" id="ca-code" name="code" pattern="[0-9]{5,6}" required '
        'inputmode="numeric"></div>'
        '<div class="frm-row">'
        '<label for="ca-ex" title="除权除息日（YYYY-MM-DD）">除权日</label>'
        '<input class="inp num" id="ca-ex" name="ex_date" pattern="\\d{4}-\\d{2}-\\d{2}" required '
        'placeholder="2026-11-20"></div>'
        '<div class="frm-row">'
        '<label for="ca-rn" title="送转全口径比分子（10送5 → 15）；split 必填">比率分子</label>'
        '<input class="inp num" id="ca-rn" name="ratio_num" inputmode="numeric" '
        'placeholder="15"></div>'
        '<div class="frm-row">'
        '<label for="ca-rd" title="送转全口径比分母（10送5 → 10）；split 必填">比分母</label>'
        '<input class="inp num" id="ca-rd" name="ratio_den" inputmode="numeric" '
        'placeholder="10"></div>'
        '<div class="frm-row">'
        '<label for="ca-pay" title="派息日（YYYY-MM-DD）；dividend 必填">派息日</label>'
        '<input class="inp num" id="ca-pay" name="pay_date" pattern="\\d{4}-\\d{2}-\\d{2}" '
        'placeholder="2026-11-27"></div>'
        '<div class="frm-row">'
        '<label for="ca-ps" title="每股税前现金（元/股）；dividend 必填">每股派息</label>'
        '<input class="inp num" id="ca-ps" name="per_share_cash" inputmode="decimal" '
        'placeholder="0.35"></div>'
        '<div class="frm-row"><label></label>'
        '<button class="btn btn-pri" type="submit">馈送入账</button></div>'
        '<p class="tbl-note" title="写者=paper 引擎（sim.lock 内直调）；决策 payload 零敏感量'
        '（NR-5）">写者=paper 引擎 · 幂等：同口径重复馈送 409 · '
        "决策 payload 零敏感量</p></form>"
        '<div id="paper-ca-result"></div>'
    )
    body = form
    if not has_positions:
        body = (
            '<details class="brief-fold"><summary class="brief-sum">'
            "公司行动馈送（需在持仓——当前不可用，提交将 422）</summary>"
            f"{form}</details>"
        )
    return _card("公司行动馈送", body, meta="手动登记送转/分红")


def render_alloc_form(*, has_ledger: bool) -> str:
    """④allocation 导入表单（0.0.31：三批建仓主链路用户入口；06 Stage D 产物零格式发明）。

    load-only 外置容器（沿 ca-form 0.0.24 先例）；文件路径为本机产品形态（与报告列表同源）。
    提交落点 #paper-alloc-result；成功窄通知 + SSE paper 事件驱动 #paper 重拉。
    """
    form = (
        '<form class="frm" hx-post="/api/panel/paper/allocation" '
        'hx-target="#paper-alloc-result" hx-swap="innerHTML" hx-disabled-elt="find button" '
        "hx-confirm='导入 allocation 建仓（新建三批 pending，确认成交前不改现金）？'>"
        '<div class="frm-row">'
        '<label for="al-file" title="vibe-stock 批量精选产物 portfolio/allocation.json'
        '（rows：code/price/stop/entry_ref/target1/hi20/lots/tranches 三批手数）">文件路径</label>'
        '<input class="inp" id="al-file" name="file" required '
        'placeholder="/Users/you/.vibe-stock/reports/_scans/…/portfolio/allocation.json"></div>'
        '<div class="frm-row">'
        '<label for="al-date" title="导入日（YYYY-MM-DD）；缺省市场今日。T1 开盘批在其后'
        '首个交易日判定（开盘 ≤ +2% 按开盘价成交、跳空转限价 5 日 GTC）">导入日</label>'
        '<input class="inp num" id="al-date" name="date" pattern="\\d{4}-\\d{2}-\\d{2}" '
        'placeholder="缺省市场今日"></div>'
        '<div class="frm-row"><label></label>'
        '<button class="btn btn-pri" type="submit">导入建仓</button></div>'
        '<p class="tbl-note" title="写者=paper 引擎（sim.lock 内 open_position）；dup code 整批拒；'
        '决策 payload 零价格零数量（NR-5）">账本缺席时以 allocation budget 起始建账 · '
        "dup code 整批拒 · 决策 payload 零敏感量</p></form>"
        '<div id="paper-alloc-result"></div>'
    )
    hint = "追加建仓（dup code 整批拒）" if has_ledger else "以 allocation budget 起始新建账本"
    return _card(
        "allocation 导入",
        form,
        meta=f"三批建仓入口 · {hint}",
    )


def _ca_basis(r: dict) -> str:
    """登记表口径列：split 比率 / dividend 每股额（公开公告参数，呈现态）。"""
    if r.get("kind") == "split":
        return f"{r.get('ratio_num')}:{r.get('ratio_den')}"
    return f"{r.get('per_share_cash')} 元/股"


def _perf_grid(perf: dict) -> str:
    """④绩效三格（Sharpe/最大回撤/胜率；None 显 —；回撤负值绿=跌语义）。"""
    sharpe = perf.get("sharpe")
    max_dd = perf.get("max_drawdown")
    win_rate = perf.get("win_rate")
    return _stat_grid(
        (
            ("Sharpe（日频年化）", sharpe if sharpe is not None else "—", ""),
            (
                "最大回撤",
                f"{max_dd * 100:.1f}%" if max_dd is not None else "—",
                "dn" if max_dd is not None else "",
            ),
            ("胜率（FIFO）", f"{win_rate:.0%}" if win_rate is not None else "—", ""),
        ),
        label="模拟盘绩效指标",
    )


_ORIGIN_ZH = {
    "gate": "准入闸拒（L2 总闸 / 每日 5 票 / 实盘优先）",
    "assert": "断言器拒（schema / 红线）",
    "render_scan": "落盘扫描拒（脱敏断言）",
    "write_failure": "写卡失败",
    "session_degraded": "会话降级",
}


def render_advice_library(payload: dict) -> str:
    """面板⑤：建议卡库（当日卡列表，code 可点开详情四块）+ 拒卡/降级通报区。

    0.0.30 两节卡片化（对齐 ④⑥ 0.0.28 卡片化基线，_card SSOT）：h3 退役由 card-h 承载；
    卡面脱敏口径 muted 注记移入建议卡卡内（§⑧ .muted「有数据时的说明小字」归位）。
    """
    cards = payload.get("cards") or []
    refused = payload.get("refused") or []
    day = payload.get("date", "")
    muted_note = (
        '<p class="muted" title="份额原值禁入落盘产物（R2/NR-5）；候选动作为 '
        'vibe-stock/04 五档分类学措辞">卡面脱敏口径：只落百分比与档位词；'
        "候选动作为五档分类学措辞。</p>"
    )
    if cards:
        rows = []
        for card in cards:
            code = _esc(card.get("code", ""))
            link = (
                f'<a href="#" class="code num" '
                f'hx-get="/api/panel/advice-detail/{_esc(day)}/{code}?mount=ad" '
                f'hx-target="#advice-detail-2" hx-swap="innerHTML show:top">{code}</a>'
            )
            reviewed = '<span class="st done">已复核</span>' if card.get("reviewed") else "—"
            mtime = str(card.get("mtime", ""))

            rows.append(
                "<tr>"
                f"<td>{link}</td>"
                f'<td title="{_esc(card.get("candidate_action", ""))}">'
                f"{_esc(card.get('candidate_action', ''))}</td>"
                f"<td>{_esc(card.get('final_signal', ''))}</td>"
                f"<td>{_esc(card.get('prev_final_signal', '') or '—')}</td>"
                f'<td class="num" title="{_esc(card.get("t1_sell_pct", ""))}% · '
                f'{_esc(card.get("t1_sell_tier") or "")}">'
                f"{_esc(card.get('t1_sell_pct', ''))}% · "
                f"{_esc(card.get('t1_sell_tier'))}</td>"
                f'<td class="num" title="{_esc(mtime)}">{_esc(_ts_hhmm(mtime))}</td>'
                f"<td>{reviewed}</td>"
                "</tr>"
            )
        lib_body = (
            '<div class="tbl-wrap sm">'
            + _table(
                "w-code|-|w-num|w-st|w-kind|w-ts|w-st",
                "代码|候选动作|终判|上一裁决|T+1 可卖:num|落盘:num|复核",
                "".join(rows),
            )
            + "</div>"
            + muted_note
        )
    else:
        lib_body = (
            _empty(
                "当日无建议卡。",
                'T2 复核未触发或被降级——可<a href="#/monitor">去③页对事件手动触发盯盘复核'
                " →</a>；降级原因见下方通报表。",
            )
            + muted_note
        )
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
                f'<td title="{_esc(_ORIGIN_ZH.get(origin, origin))}">'
                f"{_esc(_ORIGIN_ZH.get(origin, origin) or '—')}</td>"
                f'<td title="{_esc(item.get("reason_code", ""))}">'
                f"{_esc(item.get('reason_code', '') or '—')}</td>"
                f'<td title="{_esc(note)}">'
                f"{_esc(note or '—')}</td>"
                "</tr>"
            )
        ref_body = _table(
            "w-ts|w-code|w-kind|w-kind|-", "时间:num|代码|来源|原因|通报", "".join(rows)
        )
    else:
        ref_body = _empty(
            "无拒卡/降级记录。",
            "诊股/复核被拒或会话降级时在此自动通报；空表即当日无拒卡、无降级。",
            title="五源：gate 准入闸拒 / assert 断言器拒 / render_scan 落盘扫描拒 / "
            "write_failure 写卡失败 / session_degraded 会话降级",
        )
    return _card(
        f"建议卡 · {_esc(day)}",
        lib_body,
        meta="落盘投影 · 只读|monitor-advice/1 · 卡面落盘脱敏（NR-5/R2 豁免面：面板呈现态给原值）",
    ) + _card(
        "拒卡 / 降级通报",
        ref_body,
        meta="五源通报|gate 准入闸拒 / assert 断言器拒 / render_scan 落盘扫描拒 / "
        "write_failure 写卡失败 / session_degraded 会话降级",
    )


def render_audit_archive(payload: dict) -> str:
    """⑦归档索引卡：日期带 + 当日归档行（uuid/size/mtime + 查看按钮 → #au-transcript）。

    0.0.24：#au-transcript 容器外置到 index.html（此前嵌本卡内，180s/sse:decisions
    重拉会清空正在阅读的转录）；查看按钮 show:top 滚动定位。
    """
    dates = payload.get("dates") or []
    if not dates:
        return _empty(
            "暂无会话转录归档。",
            "真实 CC 会话产物经断言器放行后归档，30 天自动清理。",
            title="真实通道断言器放行后落 agent/audit/（stream-json）",
        )
    day = payload.get("date") or dates[0]
    chips = "".join(
        f'<button class="mini{" on" if d == day else ""}" '
        f'hx-get="/api/panel/audit-archive?date={_esc(d)}" hx-target="#au-archive" '
        f'hx-swap="innerHTML">{_esc(d)}</button>'
        for d in dates[:10]
    )
    files = payload.get("files") or []

    # 0.0.30：310px 边栏容不下四列（转录名曾塌缩 0 宽）——收敛两列，mtime/大小入 title
    # 0.0.34：展示名剥离与日期 chip 重复的「:YYYY-MM-DD:」段（区分度集中在尾部时戳），
    # 超长再首尾截断（全值恒在 title）
    def _disp_name(fname: str) -> str:
        disp = fname.replace(f":{day}:", ":")
        return f"{disp[:20]}…{disp[-7:]}" if len(disp) > 28 else disp

    rows = "".join(
        "<tr>"
        f'<td title="{_esc(f["file"])} · {_esc(_ts_short(f.get("mtime", "")))} · '
        f'{f["size_bytes"]:,} B"><span class="code num">{_esc(_disp_name(f["file"]))}</span></td>'
        "<td>"
        f'<button class="mini" hx-get="/api/panel/audit-archive/{_esc(day)}/{_esc(f["file"])}" '
        'hx-target="#au-transcript" hx-swap="innerHTML show:top">查看</button></td>'
        "</tr>"
        for f in files
    )
    body = _table("-|w-act", "转录|", rows) if files else _empty("当日无归档。")
    return f'<div class="chip-row">{chips}</div>' + body


def render_audit_transcript(body: dict) -> str:
    """转录 tail fragment（<pre> 原文行；NR-3 已放行产物，本地只读重放）。"""
    lines = body.get("lines") or []
    total = body.get("total_lines", 0)
    shown = f"（尾 {len(lines)} / 共 {total} 行）" if len(lines) < total else f"（共 {total} 行）"
    text = _esc("\n".join(lines))
    fname = str(body.get("file", ""))
    # 卡头长名截断防溢出卡框（310px 边栏；全值入 title，沿 hash 列 h[:12] 先例）
    fname_short = f"{fname[:20]}…{fname[-6:]}" if len(fname) > 28 else fname
    return _card(
        f"转录 · {_esc(fname_short)}",
        f'<pre class="md-pre">{text}</pre>',
        meta=f"stream-json {shown}|{_esc(fname)}",
    )


def render_sessions_table(sessions: dict) -> str:
    """⑦会话登记表：uuid/task_id/tokens/等效 USD/转录路径（审计三件套的 sessions 腿呈现）。

    固定列宽 + 单行省略 + title 悬浮全值（UI 表格规范）；
    sessions/budget 属运营元数据（NR-5 豁免面）。
    """
    rows_map = (sessions.get("sessions") or {}) if isinstance(sessions, dict) else {}
    records = sorted(rows_map.values(), key=lambda r: str(r.get("ts", "")), reverse=True)
    if not records:
        return _empty("暂无会话登记。")
    trs = []
    for r in records[:30]:
        tokens = (r.get("usage_tokens") or {}).get("total", 0)
        uuid = str(r.get("session_uuid", ""))
        task = str(r.get("task_id", ""))
        transcript = str(r.get("transcript_path", ""))
        link = ""
        if "/audit/" in transcript:  # 三件套互查：sessions 行 → audit 转录查看（0.0.21）
            tail_path = transcript.split("/audit/", 1)[1]
            date_part, _, fname = tail_path.partition("/")
            if date_part and fname:
                link = (
                    f'<a href="#" hx-get="/api/panel/audit-archive/{_esc(date_part)}/'
                    f'{_esc(fname)}" hx-target="#au-transcript" hx-swap="innerHTML show:top">'
                    f"{_esc(fname[:26])}…</a>"
                )
        cell = link or f'<span title="{_esc(transcript)}">{_esc(transcript[:28])}…</span>'
        ts = str(r.get("ts", ""))
        trs.append(
            "<tr>"
            f'<td class="num" title="{_esc(ts)}">{_esc(_ts_short(ts))}</td>'
            f'<td title="{_esc(uuid)}"><span class="code num">{_esc(uuid[:14])}…</span></td>'
            f'<td title="{_esc(task)}"><span class="code num">{_esc(task[:24])}…</span></td>'
            f'<td class="num">{tokens:,}</td>'
            f'<td class="num">{float(r.get("cost_usd_equiv") or 0):.2f}</td>'
            f'<td title="{_esc(transcript)}"><span class="code num">{cell}</span></td>'
            "</tr>"
        )
    return (
        '<div class="tbl-wrap sm">'
        + _table(
            "w-sess-ts|w-uuid|w-task|w-tok|w-usd|-",
            "时间:num|会话|任务|token:num|等效 USD:num|转录路径",
            "".join(trs),
        )
        + "</div>"
        + '<p class="tbl-note" title="audit/ stream-json 归档 30 天官方清理（F-38）">'
        "三件套互查：decisions（hash 链）× sessions × audit/ stream-json 归档"
        "（30 天自动清理）</p>"
    )


def render_submit_result(
    task: dict | None, *, status: int, detail: str, session_target: str | None = "#dg-session"
) -> str:
    if task is not None and status < 300:
        return (
            f'<div id="submit-result">{render_task_card(task, session_target=session_target)}</div>'
        )
    return (
        f'<div class="task-card" id="submit-result">'
        f'<p class="warn">提交被拒（{status}）：{_esc(detail)}</p></div>'
    )


def render_error_card(status: int, detail: str, *, retry_path: str = "") -> str:
    """统一 HTML 错误卡（app 级异常处理器 → /api/panel/* htmx 面；状态码保留）。

    GET 附「重试」（错误卡已被换入目标容器，closest div[id] 即容器自身）；
    POST 不附——重按原按钮即重试。detail 全程 _esc。
    """
    retry = (
        f'<button class="mini" hx-get="{_esc(retry_path)}" '
        'hx-target="closest div[id]" hx-swap="innerHTML">重试</button>'
        if retry_path
        else ""
    )
    return (
        f'<div class="task-card"><p class="warn">操作未生效（HTTP {int(status)}）：'
        f"{_esc(detail)}</p>{retry}</div>"
    )
