"""会话中间产物解析（P2 五段可观测性；12-orchestration §⑫ 案 a 会后解析）。

数据面（探索实证）：真实 CC 会话 cwd=agent/runs/<uuid>/，skill 以 .vs 相对执行——
`.vs/{evidence.json, opinions.json, opinions.position.json, verdict.json,
verdict.position.json, context-pack.md, risk.json, outlook.json}` 全留（两仓零清理）。
纪律：外仓 schema defensive 解析缺席降级不炸页（R-03 同款）；产品仓只读。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stock_trading_agent.server.panel_data import load_root_json

STAGES = ("取证据", "上下文包", "意见", "裁决", "报告")


@dataclass
class SessionView:
    session_uuid: str
    workdir: Path
    evidence: dict | None = None
    opinions_default: list | None = None
    opinions_position: list | None = None
    verdict_default: dict | None = None
    verdict_position: dict | None = None
    report_path: Path | None = None
    notes: list[str] = field(default_factory=list)


def read_session_products(runs_dir: Path, session_uuid: str, reports_dir: Path) -> SessionView:
    """读 `.vs/` 中间产物（defensive：缺席降级空，.vs 缺失=空 workdir）。"""
    view = SessionView(session_uuid=session_uuid, workdir=runs_dir / session_uuid / ".vs")
    vs = view.workdir
    if not vs.exists():
        view.notes.append("workdir 不存在（dry-run 通道无真实会话产物；或会话未跑至该阶段）")
        return view
    ev = load_root_json(vs / "evidence.json")
    view.evidence = ev if isinstance(ev, dict) else None
    ops = load_root_json(vs / "opinions.json")
    view.opinions_default = ops if isinstance(ops, list) else None
    ops_p = load_root_json(vs / "opinions.position.json")
    view.opinions_position = ops_p if isinstance(ops_p, list) else None
    vd = load_root_json(vs / "verdict.json")
    view.verdict_default = vd if isinstance(vd, dict) else None
    vd_p = load_root_json(vs / "verdict.position.json")
    view.verdict_position = vd_p if isinstance(vd_p, dict) else None
    meta = (view.evidence or {}).get("meta")
    if isinstance(meta, dict):
        code, market = str(meta.get("code") or ""), str(meta.get("market") or "")
        if code and market:
            day_dir = reports_dir / f"{code}.{market}"
            if day_dir.exists():
                latest = sorted(day_dir.glob("*.md"))
                view.report_path = latest[-1] if latest else None
    return view


def stage_status(view: SessionView) -> list[dict]:
    """五段状态机：evidence 落地→①②done；opinions 非空→③；verdict→④；报告→⑤。

    坑位：opinions.json 由 prepare 铺底 `[]`（LLM 覆写后才非空）——「存在」≠「已产意见」，
    非空数组才算 ③ 完成；首个未完成段=current。
    """
    states: list[dict] = []
    ev_ok = view.evidence is not None
    ops_ok = bool(view.opinions_default)  # 非空数组
    vd_ok = view.verdict_default is not None
    rp_ok = view.report_path is not None
    flags = [
        (1, STAGES[0], ev_ok, _evidence_note(view)),
        (
            2,
            STAGES[1],
            ev_ok and (view.workdir / "context-pack.md").exists(),
            "context-pack.md 在场",
        ),
        (
            3,
            STAGES[2],
            ops_ok,
            f"意见 {len(view.opinions_default or [])} 条"
            + (f" · 中线 {len(view.opinions_position or [])} 条" if view.opinions_position else ""),
        ),
        (4, STAGES[3], vd_ok, _verdict_note(view)),
        (5, STAGES[4], rp_ok, view.report_path.name if rp_ok else "reports 未出"),
    ]
    current_found = False
    for stage, name, done, note in flags:
        state = "done" if done else ("current" if not current_found else "pending")
        if not done:
            current_found = True
        states.append({"stage": stage, "name": name, "state": state, "note": note})
    return states


def _evidence_note(view: SessionView) -> str:
    quality = (view.evidence or {}).get("quality") or {}
    if not quality:
        return "evidence.json 在场"
    return f"质量分 {quality.get('score', '—')} · 置信帽 {quality.get('confidence_cap', '—')}"


def _verdict_note(view: SessionView) -> str:
    vd = view.verdict_default or {}
    rg = vd.get("risk_guard") or {}
    final = rg.get("final") or (vd.get("aggregate") or {}).get("final_signal")
    return f"final {final or '—'}" + (
        " · RiskGuard 降档" if rg.get("initial") != rg.get("final") else ""
    )


def opinions_board(view: SessionView) -> dict:
    """双轨意见板：default 逐卡（source/signal/confidence/weight），position 折叠说明。"""

    def _cards(rows: list | None) -> list[dict]:
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "source": str(row.get("source", "")),
                    "signal": row.get("signal"),
                    "confidence": row.get("confidence"),
                    "weight": row.get("weight"),
                    "rationale": str(row.get("rationale", ""))[:120],
                }
            )
        return out

    return {
        "default": _cards(view.opinions_default),
        "position": _cards(view.opinions_position),
    }


def verdict_card(view: SessionView) -> dict | None:
    """裁决卡：initial→RiskGuard 降档链→final、分歧三桶、置信帽、证据质量。"""
    vd = view.verdict_default
    if not isinstance(vd, dict):
        return None
    rg = vd.get("risk_guard") or {}
    agg = vd.get("aggregate") or {}
    disagreement = vd.get("disagreement") or {}
    buckets = disagreement.get("buckets") or {}
    return {
        "initial": rg.get("initial"),
        "final_before_cap": rg.get("final_before_cap"),
        "final": rg.get("final"),
        "risk_guard_log": [str(x) for x in (rg.get("log") or [])],
        "confidence": agg.get("confidence"),
        "consensus_level": agg.get("consensus_level"),
        "buckets": {k: list(v) for k, v in buckets.items() if isinstance(v, list)},
        "evidence_quality": vd.get("evidence_quality") or {},
        "executable": vd.get("executable"),
    }
