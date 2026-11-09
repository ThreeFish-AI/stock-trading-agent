"""仓级 selftest（与 pytest 同源判据）：纯函数回归 case 注册表。

判据 SSOT 见 AGENTS.md 测试门禁节（全绿才可提交、判据只收严）；
case 名即 CI 输出契约，增删改名均属门禁变更。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import plistlib
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_trading_agent.budget.gate import BudgetConfig, check, record_dual_axis
from stock_trading_agent.cc_client.cli import build_argv
from stock_trading_agent.cc_client.contract import (
    CCRequest,
    ContractError,
    derive_session_uuid,
    validate_request,
)
from stock_trading_agent.loop.assertor import assess_result
from stock_trading_agent.loop.events import EventEnvelope, gate_report_event, lock_precheck
from stock_trading_agent.loop.monitor_events import MonitorEvent
from stock_trading_agent.loop.router import load_default_table
from stock_trading_agent.paper.batches import derive_position_status
from stock_trading_agent.paper.fills import Bar, match_limit_buy, t1_gap_fallback_limit
from stock_trading_agent.state.decisions import DecisionLog
from stock_trading_agent.state.queue import QueueError, QueueState, TaskRecord


def _repo_root():
    return Path(__file__).resolve().parents[2]


_CASES: list[tuple[str, object]] = []


def _case(name: str):
    """注册 selftest case（装饰器执行序即注册序，与原 check_case 调用序一致）。"""

    def deco(fn):
        _CASES.append((name, fn))
        return fn

    return deco


def run_selftests() -> list[tuple[str, bool, str]]:
    """按注册序执行全部 case；异常即 FAIL（判据与 pytest 同源，AGENTS.md 测试门禁）。"""
    results: list[tuple[str, bool, str]] = []
    for name, fn in _CASES:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:
            results.append((name, False, str(exc)))
    return results


# --- 共享 fixture（case 间去重；覆写参数即差异点） ---


def _ccreq(**overrides) -> CCRequest:
    """CCRequest 构造默认值（NR-4 必填字段齐全，case 内按需覆写）。"""
    return CCRequest(
        **{
            "task_id": "t",
            "session_uuid": "u",
            "prompt": "/vibe-stock 600519",
            "schema": None,
            "budget_usd": 1.0,
            "max_turns": 10,
            "allowed_tools": ("Read",),
        }
        | overrides
    )


def _mev(**overrides) -> MonitorEvent:
    """MonitorEvent 构造默认值（T0 真实事件基线，case 内按需覆写）。"""
    return MonitorEvent(
        **{
            "ts": "t",
            "code": "600519",
            "market": "cn",
            "kind": "real",
            "rule": "r",
            "level": "T0",
            "notify": True,
        }
        | overrides
    )


def _advice_payload(**overrides) -> dict:
    """T2 建议卡结构化 payload 基线（advice/panel 两 case 共用）。"""
    return {
        "outcome": "card",
        "candidate_action": "持有观察",
        "final_signal": "hold",
        "prev_diff": {"prev_final_signal": "none", "note": "n"},
        "t1_sell_pct": 85.0,
        "t1_sell_tier": "大部分",
        "verdict_date": "d",
        "opinion_data_date": "d",
        "replayed_opinions": 2,
        "trend_ref": {
            "band_rows": [{"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0}]
        },
        "rationale": "r",
    } | overrides


@_case("cc_client.contract.uuidv5")
def uuid_deterministic():
    a = derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
    b = derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
    assert a == b and a != derive_session_uuid("2026-11-07", "600519", "diagnose_full")


@_case("cc_client.contract.nr4")
def nr4_gate():
    req = _ccreq(task_id="t1", session_uuid=derive_session_uuid("d", "c", "k"))
    validate_request(req)
    for bad in (0.0, -1.0):
        try:
            validate_request(_ccreq(budget_usd=bad))
            raise AssertionError("NR-4 未拦截 budget_usd<=0")
        except ContractError:
            pass


@_case("cc_client.cli.argv")
def argv_snapshot():
    req = _ccreq(budget_usd=5, max_turns=40, allowed_tools=("Read", "Bash"))
    argv = build_argv(req)
    for flag in (
        "-p",
        "--session-id",
        "--max-budget-usd",
        "--max-turns",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--strict-mcp-config",
        "--system-prompt-snapshot",
    ):
        assert any(a == flag or a.startswith(flag) for a in argv), f"缺旗标 {flag}"
    assert argv[-1] == "/vibe-stock 600519"


# env_sanitize 用例见 tests/unit/test_cc_client_contract.py（R2 机器门限定密钥名只在 cli.py）


@_case("paper.fills.strict_cross")
def strict_cross():
    bar = Bar(date="2026-11-07", open=10.0, high=10.2, low=9.5, close=9.8)
    assert match_limit_buy("600519", 1, 9.5, bar) is None  # low == limit 不成交（两把尺）
    fill = match_limit_buy("600519", 1, 9.6, bar)
    assert fill is not None and fill.price == 9.6
    open_cap = match_limit_buy("600519", 1, 99.0, bar)
    assert open_cap is not None and open_cap.price == 10.0  # 价 = min(open, limit)


@_case("paper.batches.position")
def position_machine():
    from stock_trading_agent.paper.model import Tranche

    t = [Tranche(leg="T1_open", lots=1, ref_price=10.0)]
    assert derive_position_status(t) == "pending_open"
    assert derive_position_status([t[0].model_copy(update={"status": "filled"})]) == "holding"
    assert derive_position_status([t[0].model_copy(update={"status": "expired"})]) == "closed"


@_case("paper.fills.t1_gap")
def gap_fallback():
    assert t1_gap_fallback_limit(10.0) == 10.2


@_case("state.queue.idem_wal")
def queue_idem():
    q = QueueState()
    q.submit(
        TaskRecord(
            task_id="a", dedup_key="k:600519:2026-11-07", kind="diagnose_quick", code="600519"
        )
    )
    try:
        q.submit(
            TaskRecord(
                task_id="b",
                dedup_key="k:600519:2026-11-07",
                kind="diagnose_quick",
                code="600519",
            )
        )
        raise AssertionError("幂等键未拦截")
    except QueueError:
        pass
    q.transition("a", "running")
    q.transition("a", "done")
    try:
        q.transition("a", "running")
        raise AssertionError("终态回退未拦截")
    except QueueError:
        pass


@_case("state.decisions.hash_chain")
def hash_chain():
    log = DecisionLog()
    log.append("2026-11-07T00:00:00Z", "task_done", {"task_id": "t"})
    log.append("2026-11-07T00:00:01Z", "task_done", {"task_id": "t2"})
    assert log.verify_chain()
    log.lines[0].payload["task_id"] = "tampered"
    assert not log.verify_chain()


@_case("state.decisions.tail_sync_repair")
def tail_sync_and_repair():

    from stock_trading_agent.state.decisions import (
        DecisionChainError,
        repair_chain_lines,
    )
    from stock_trading_agent.state.store import atomic_write_text

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "decisions.jsonl"
        a = DecisionLog()
        a.append("2026-11-07T00:00:00Z", "task_submitted", {"task_id": "t"}, path=path)
        b = DecisionLog()  # 重启/降级态：空内存实例
        b.append("2026-11-07T00:00:01Z", "task_done", {"task_id": "t"}, path=path)
        reloaded = DecisionLog.load(path)
        assert [x.seq for x in reloaded.lines] == [1, 2]  # 空链直写不再回退 seq（W1 防回退）
        # 构造断链样本（模拟 W1 产物：两段各自从 seq=1）→ 保行重链
        raw = path.read_text(encoding="utf-8").splitlines()
        c = DecisionLog()
        c.append("2026-11-07T00:00:02Z", "task_done", {"task_id": "t2"})
        raw.append(json.dumps(asdict(c.lines[0]), ensure_ascii=False))
        atomic_write_text(path, "\n".join(raw) + "\n")
        try:
            DecisionLog.load(path)
            raise AssertionError("断链样本应 raise")
        except DecisionChainError:
            pass
        rows, stats = repair_chain_lines(raw, ts="2026-11-08T00:00:00Z", old_sha256="0" * 64)
        assert stats.rows_after == stats.rows_before + 2
        atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        assert DecisionLog.load(path).verify_chain()  # 修复后可载入且链完整


@_case("loop.router.table")
def routing_table():
    table = load_default_table()
    assert table.route("diagnose_quick") is not None
    assert table.route("nonexistent") is None
    assert table.fallback_count == 1


@_case("loop.events.window_lock")
def no_report_window():

    tz = ZoneInfo("Asia/Shanghai")
    ev = EventEnvelope(event_id="e", source="panel", kind="briefing_review", code="600519")
    # 窗口按市场本地钟面（Asia/Shanghai）：UTC 传入须换算（防 8 小时错位）
    assert gate_report_event(dt.datetime(2026, 11, 7, 17, 45, tzinfo=tz), ev.kind) is not None
    utc_in_window = dt.datetime(2026, 11, 7, 9, 45, tzinfo=dt.UTC)
    assert gate_report_event(utc_in_window, ev.kind) is not None
    assert gate_report_event(dt.datetime(2026, 11, 7, 19, 0, tzinfo=tz), ev.kind) is None
    assert gate_report_event(dt.datetime(2026, 11, 7, 17, 45, tzinfo=dt.UTC), ev.kind) is None
    assert gate_report_event(dt.datetime(2026, 11, 7, 17, 45, tzinfo=tz), "diagnose_quick")
    assert lock_precheck(fetch_lock=True, watch_lock=False) is not None


@_case("loop.assertor.verdicts")
def assertor_verdicts():

    from stock_trading_agent.cc_client.contract import CCResult

    ok_result = CCResult(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output={"signal": "hold", "disclaimer": "x"},
        result_line_present=True,
        transcript_path=Path("/dev/null"),
    )
    assert assess_result(ok_result, "diagnose_quick").ok
    bad = CCResult(
        exit_code=0,
        envelope={"subtype": "success"},
        structured_output=None,
        result_line_present=True,
        transcript_path=Path("/dev/null"),
    )
    assert not assess_result(bad, "diagnose_quick").ok  # success 缺 structured_output = 失败


@_case("budget.gate.axes")
def budget_gates():
    cfg = BudgetConfig(enabled=True, billing_mode="api")
    assert not check(
        cfg, session_est_usd=2.0, day_spent_usd=0, month_spent_usd=0, day_task_count=0
    ).allowed
    assert not check(
        cfg, session_est_usd=0.5, day_spent_usd=8.0, month_spent_usd=0, day_task_count=0
    ).allowed
    sub = BudgetConfig(enabled=True, billing_mode="subscription")
    v = check(sub, session_est_usd=999, day_spent_usd=999, month_spent_usd=9999, day_task_count=0)
    assert v.allowed and v.record_only
    led = record_dual_axis({"input_tokens": 10}, 0.5, {}, day="2026-11-08", month="2026-11")
    assert led["tokens"]["input_tokens"] == 10 and led["tasks"] == 1


@_case("loop.monitor_events.t1_trigger")
def monitor_event_parsing():
    from stock_trading_agent.loop.monitor_events import parse_line, t1_trigger

    good = parse_line(
        '{"ts":"09:31:00","code":"600519","market":"cn","kind":"real","rule":"stop_break",'
        '"level":"T0","notify":true,"mech_risk":{"downgrade":true,"severity":"high"},'
        '"facts":{"pct":-2.1},"review":"止损复核"}'
    )
    assert good is not None and good.code == "600519" and good.review == "止损复核"
    assert parse_line("{截断") is None and parse_line("") is None  # 坏行不抛
    assert t1_trigger(good)
    assert not t1_trigger(_mev(suppress_reason="cooldown", review="止损复核"))
    assert not t1_trigger(_mev(code="*", kind="*"))


@_case("server.schemas.reject_status")
def reject_status_map():
    from stock_trading_agent.loop.loop import RejectReason
    from stock_trading_agent.server.schemas import REJECT_STATUS

    assert REJECT_STATUS[RejectReason.DUPLICATE] == 409
    assert REJECT_STATUS[RejectReason.LOCK] == 423
    assert REJECT_STATUS[RejectReason.WINDOW] == 423
    assert REJECT_STATUS[RejectReason.UNKNOWN_KIND] == 422


@_case("loop.worker.task_eligible")
def task_eligible_matrix():

    from stock_trading_agent.loop.worker import task_eligible
    from stock_trading_agent.state.queue import TaskRecord

    t1 = TaskRecord(task_id="a", dedup_key="k", kind="t1_refresh", code="600519")
    rep = TaskRecord(task_id="b", dedup_key="k2", kind="diagnose_quick", code="600519")
    assert task_eligible(t1, fetch_lock=True, watch_lock=False, now=dt.datetime.now(tz=dt.UTC))
    assert task_eligible(t1, fetch_lock=False, watch_lock=True, now=dt.datetime.now(tz=dt.UTC))
    free = dt.datetime(2026, 11, 7, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert task_eligible(rep, fetch_lock=False, watch_lock=False, now=free) is None
    win = dt.datetime(2026, 11, 7, 17, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert task_eligible(rep, fetch_lock=False, watch_lock=False, now=win)


@_case("cc_client.cli.parse_stream")
def cc_normalize_and_parse():
    from stock_trading_agent.cc_client.cli import _parse_stream, normalize_exit_code

    assert normalize_exit_code(-15) == 143 and normalize_exit_code(-9) == 137
    assert normalize_exit_code(-2) == 130 and normalize_exit_code(0) == 0
    init = (
        '{"type":"system","subtype":"init","session_id":"s","capabilities":{},'
        '"mcp_server_errors":[]}'
    )
    ok = '{"type":"result","subtype":"success","session_id":"s","usage":{"input_tokens":1}}'
    parsed = _parse_stream(["{坏行", init, ok])
    assert parsed.result_line_present and parsed.session_id == "s"
    assert _parse_stream([init]).envelope is None  # 缺 result 行


@_case("budget.gate.billing_from_probe")
def billing_from_probe():
    from stock_trading_agent.budget.gate import billing_mode_from_probe

    assert billing_mode_from_probe("subscription") == "subscription"
    assert billing_mode_from_probe("api") == "api"
    assert billing_mode_from_probe("dry-run") == "api"  # 判不准收紧（D2）


@_case("ops.launchd.plist")
def launchd_plist_pinned():

    plist_path = _repo_root() / "ops" / "launchd" / "com.threefish.stocktrading.agent.plist"
    with plist_path.open("rb") as fh:
        data = plistlib.load(fh)
    assert data["KeepAlive"] is True
    assert data["AbandonProcessGroup"] is True
    assert data["ExitTimeOut"] == 20 and data["ThrottleInterval"] == 30
    assert data["RunAtLoad"] is True and "serve" in data["ProgramArguments"]


@_case("loop.advice.card_scan")
def advice_constants_and_scan():
    from stock_trading_agent.loop.advice import (
        ADVICE_ACTIONS,
        render_advice_card,
        scan_advice_text,
    )
    from stock_trading_agent.loop.monitor_events import market_today

    assert ADVICE_ACTIONS == ("可加仓", "持有", "持有·等回踩", "持有观察", "减仓", "离场")
    payload = _advice_payload(verdict_date=market_today(), opinion_data_date=market_today())
    md = render_advice_card(payload, code="600519", date=market_today(), task_id="t")
    assert scan_advice_text(md) == []
    bad = render_advice_card(
        {**payload, "rationale": "卖 1200 股"}, code="600519", date="d", task_id="t"
    )
    assert scan_advice_text(bad)  # 份额原值负断言命中


@_case("loop.advice.anchor_gate")
def advice_anchor_and_gate():
    from stock_trading_agent.loop.advice import t2_admission
    from stock_trading_agent.loop.assertor import _SIGNAL_RANK, AdviceContext
    from stock_trading_agent.state.queue import TaskRecord

    assert _SIGNAL_RANK["strong_buy"] < _SIGNAL_RANK["hold"] < _SIGNAL_RANK["strong_sell"]
    task = TaskRecord(task_id="t", dedup_key="k", kind="monitor_review", code="600519")
    assert t2_admission(task, done_count=4, real_codes=None) is None
    assert t2_admission(task, done_count=5, real_codes={"600519"}) is None
    assert t2_admission(task, done_count=5, real_codes={"601919"}) is not None
    ctx = AdviceContext(today="2026-11-08", last_signal="hold", signals_read_ok=True)
    assert ctx.last_signal == "hold"


@_case("paper.tplus1_band")
def paper_tplus1_and_band():
    from stock_trading_agent.paper.fills import Bar
    from stock_trading_agent.paper.ledger import open_position
    from stock_trading_agent.paper.model import Operation, SimPortfolio
    from stock_trading_agent.paper.rules import band_state, limit_band
    from stock_trading_agent.paper.tplus1 import available_lots

    band = limit_band(9.99)
    assert band.upper == 10.98 and band.lower == 9.00  # 板价向内取整（E-09）
    locked = Bar("d", 11.0, 11.0, 11.0, 11.0, prev_close=10.0)
    assert band_state(locked, limit_band(10.0)) == "locked_up"  # 一字板

    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = open_position(pf, "600519", "A", [], entry_ref=10.0)
    pf.position("600519").lots = 3
    pf.operations = [
        Operation(
            code="600519",
            side="buy",
            lots=2,
            price=10.0,
            ts="2026-11-08T02:00:00+00:00",
            trade_date="2026-11-08",
        )
    ]
    assert available_lots(pf, "600519", "2026-11-08") == 1  # 当日买入扣减
    assert available_lots(pf, "600519", "2026-11-09") == 3  # 日切清零


@_case("paper.tax_roundlot")
def paper_tax_and_roundlot():
    from stock_trading_agent.paper.corporate_actions import dividend_tax_rate
    from stock_trading_agent.paper.rules import assert_round_lot

    assert dividend_tax_rate(30) == 0.20 and dividend_tax_rate(31) == 0.10
    assert dividend_tax_rate(365) == 0.10 and dividend_tax_rate(366) == 0.0
    assert assert_round_lot(1) == 1
    try:
        assert_round_lot(0)
        raise AssertionError("整手闸未拦截 0 手")
    except ValueError:
        pass


@_case("paper.equity_baseline")
def paper_equity_baseline():
    from stock_trading_agent.paper.engine import mark_equity
    from stock_trading_agent.paper.ledger import open_position
    from stock_trading_agent.paper.model import SimPortfolio

    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = open_position(pf, "600519", "A", [], entry_ref=10.0)
    pf.position("600519").lots = 2
    pf = mark_equity(pf, "2026-11-08", {"600519": 10.5})
    point = pf.equity_history[-1]
    assert point.market_value == 2100 and point.equity == 102_100
    assert point.nav == 1.021
    assert pf.position("600519").last_price == 10.5


@_case("paper.pending_roundtrip")
def paper_pending_roundtrip():

    from stock_trading_agent.paper.confirm import request_from_fill
    from stock_trading_agent.paper.fills import Fill
    from stock_trading_agent.paper.pending import PendingQueue
    from stock_trading_agent.paper.pending import load as pload
    from stock_trading_agent.paper.pending import save as psave

    fill = Fill(
        code="600519", side="buy", lots=1, price=9.6, date="2026-11-08", tranche_leg="T2_limit"
    )
    req = request_from_fill(fill, request_id="s1", ref_price=9.6, ts="t")
    path = Path(tempfile.mkdtemp()) / "sim-pending-fills.json"
    psave(PendingQueue().enqueue(req), path)
    loaded = pload(path)
    assert loaded.requests[0].request_id == "s1" and loaded.requests[0].bar.date == "2026-11-08"
    assert loaded.frozen_sell_lots("600519") == 0


@_case("server.sse.event_map")
def sse_event_mapping():

    from stock_trading_agent.server.stream_hub import to_event_name
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout(root=Path("/tmp/sse-test"))
    assert to_event_name(layout.root / "agent" / "queue.json", layout) == "queue"
    assert to_event_name(layout.root / "monitor" / "events" / "d.jsonl", layout) == "monitor"
    assert to_event_name(layout.root / "sim-portfolio.json", layout) == "paper"


@_case("server.stats_briefing.scan")
def stats_and_briefing_scan():
    from stock_trading_agent.loop.briefing import build_card_md, scan_briefing_text
    from stock_trading_agent.server.stats import aggregate_decisions

    agg = aggregate_decisions([{"kind": "task_done"}, {"kind": "task_failed"}])
    assert agg["total"] == 2 and agg["success_rate"] == 0.5
    assert not agg["sufficient_sample"]  # 样本 <30 只观察
    md = build_card_md({"briefing": "收盘跌破 MA20。"}, date="2026-11-08", code="600519")
    assert scan_briefing_text(md) == []
    bad = build_card_md({"briefing": "卖 300 股"}, date="d", code="c")
    assert scan_briefing_text(bad)


@_case("ops.d8_probe.parsers")
def d8_probe_parsers():

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
    import d8_probe

    line = "~".join(
        ['v_r_hk00700="100', "腾讯控股", "00700", "319.000", "318.000"]
        + [""] * 25
        + ["2026/11/06 16:00:03", "-0.235", "-2.47", "319.400", "315.600"]
    )
    parsed = d8_probe.parse_qt_r_hk(line)
    assert parsed is not None and parsed["price"] == 319.0
    assert parsed["high"] == 319.4 and parsed["low"] == 315.6  # 实测位序 33/34
    assert parsed["ts_raw"] == "2026/11/06 16:00:03" and parsed["lag_min"] is not None
    assert d8_probe.parse_sina_rt_hk('var x="腾讯,318,319,319.5,321,316,";') == 319.5
    em = d8_probe.parse_eastmoney({"f43": 9.295, "f86": 1793952530})
    assert em is not None and em["price"] == 9.295 and em["ts"] is not None


@_case("loop.l2.t2_worthy_config")
def l2_t2_worthy_and_config():

    from stock_trading_agent.loop.monitor_events import t2_worthy
    from stock_trading_agent.state.agent_config import AgentConfig, load_agent_config

    assert t2_worthy(_mev(rule="target_hit"))
    assert not t2_worthy(_mev(rule="storm_digest"))
    assert AgentConfig().l2_enabled is False  # 安全缺省
    d = Path(tempfile.mkdtemp())
    assert load_agent_config(d / "none.json").l2_enabled is False


@_case("server.origin_guard.allowed")
def l2_origin_guard():
    from stock_trading_agent.server.origin_guard import origin_allowed

    assert origin_allowed({"origin": "http://127.0.0.1:8730"})
    assert origin_allowed({"origin": "http://testserver"})
    assert not origin_allowed({})
    assert not origin_allowed({"origin": "https://evil.example"})


@_case("cc_client.leak.scan")
def l2_leak_scan():
    from stock_trading_agent.cc_client.leak import find_orphans, parse_pgrep_output

    entries = parse_pgrep_output("123 claude -p x\nbad\n456 claude -p y\n")
    assert entries == [(123, "claude -p x"), (456, "claude -p y")]
    assert find_orphans(entries, {123}) == [(456, "claude -p y")]


@_case("panel.phase_trend")
def panel_phase_and_trend():

    from stock_trading_agent.loop.advice import parse_trend_table, render_advice_card
    from stock_trading_agent.server.panel_data import market_phase

    tz = ZoneInfo("Asia/Shanghai")
    assert market_phase(dt.datetime(2026, 11, 9, 10, 0, tzinfo=tz))["overall"] == "live"
    assert market_phase(dt.datetime(2026, 11, 8, 10, 0, tzinfo=tz))["overall"] == "closed"
    md = render_advice_card(_advice_payload(t1_sell_tier="b"), code="c", date="d", task_id="t")
    rows = parse_trend_table(md)
    assert rows and rows[0]["low"] == 9.5


@_case("panel.paper_totals")
def paper_totals_anchor():
    from stock_trading_agent.server.stats import paper_totals

    t = paper_totals([{"market_value": 1100.0}, {"market_value": 460.0}], 60_000.0, 100_000.0)
    assert t == {"market_value": 1560.0, "cash": 60000.0, "total": 61560.0, "nav": 0.6156}


@_case("loop.briefing.slots_auto")
def briefing_slots_and_auto():

    from stock_trading_agent.loop.briefing import (
        build_card_md,
        has_ack,
        plan_auto_briefings,
        slot_of_kind,
    )

    assert slot_of_kind("briefing_morning") == "am"
    md = build_card_md({"briefing": "x"}, date="2026-11-09", code="600519", slot="pm")
    assert "session: pm" in md and "盘后复盘" in md
    ack_lines = [{"kind": "briefing_ack", "payload": {"date": "d", "code": "c", "session": "pm"}}]
    assert has_ack(ack_lines, date="d", code="c", slot="pm")
    assert not has_ack(ack_lines, date="d", code="c", slot="am")
    monday_am = dt.datetime(2026, 11, 9, 9, 10, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    planned = plan_auto_briefings(
        monday_am, l2_enabled=True, universe_codes=["600519"], already_queued=set()
    )
    assert planned == [("briefing_morning", "600519")]
    assert (
        plan_auto_briefings(monday_am, l2_enabled=False, universe_codes=["c"], already_queued=set())
        == []
    )


@_case("paper.corporate.split")
def corporate_split_anchor():
    from stock_trading_agent.paper import ledger as paper_ledger
    from stock_trading_agent.paper.corporate_actions import SplitAction, apply_corporate_action
    from stock_trading_agent.paper.model import SimPortfolio, Tranche

    pf = SimPortfolio(budget=100_000, cash=100_000)
    pf = paper_ledger.open_position(
        pf, "600519", "A", [Tranche(leg="T2_limit", lots=2, ref_price=10.0)], entry_ref=10.0
    )
    pos = pf.position("600519")
    pos.lots, pos.avg_cost = 2, 10.0
    pf = apply_corporate_action(
        pf, SplitAction(code="600519", ratio_num=15, ratio_den=10, ex_date="2026-11-20")
    )
    assert pf.position("600519").lots == 3  # Decimal 2×1.5 向下取整
    assert len(pf.corporate_actions) == 1


@_case("server.metrics.pipeline")
def metrics_pipeline_anchor():
    from stock_trading_agent.server.stats import assertion_buckets, route_hit_rate

    lines = [
        {"kind": "task_submitted", "payload": {}},
        {"kind": "assert_refuse", "payload": {"reason_codes": ["exit_nonzero"]}},
        {"kind": "assert_refuse", "payload": {"reasons": ["legacy"]}},
        {
            "kind": "task_failed",
            "payload": {"reason_code": "route_unknown"},
        },
    ]
    assert assertion_buckets(lines) == {"exit_nonzero": 1, "uncategorized": 1}
    r = route_hit_rate(lines)
    assert r["submitted"] == 1 and r["fallback"] == 1 and r["rate"] == 0.0


@_case("state.queue.find_any")
def queue_find_any():
    from stock_trading_agent.state.queue import QueueState, TaskRecord

    q = QueueState()
    q.submit(
        TaskRecord(
            task_id="a",
            dedup_key="t1_refresh:600519:2026-11-08",
            kind="t1_refresh",
            code="600519",
        )
    )
    q.transition("a", "running")
    q.transition("a", "done")
    assert q.find_any("t1_refresh:600519:2026-11-08") is not None  # 终态也命中（每股每日一次）
    assert q.find_any("t1_refresh:600519:2026-11-09") is None
    assert q.find_active("t1_refresh:600519:2026-11-08") is None


@_case("server.port_guard.probe")
def port_guard_probe():
    from stock_trading_agent.server.app import _is_panel_health

    assert _is_panel_health({"ok": True, "component": "stock-trading-agent-panel", "v": "x"})
    assert not _is_panel_health({"ok": True, "component": "other-service"})
    assert not _is_panel_health("stock-trading-agent-panel")  # 非 dict 判否


# --- 0.0.15 修复轮独有 case（merge 自修复线；API 已对齐尾对账版） ---
@_case("state.decisions.repair_plan")
def decisions_repair_plan():
    from stock_trading_agent.state.decisions import DecisionLog, plan_repair

    log = DecisionLog()
    log.append("2026-11-07T00:00:00Z", "task_done", {"task_id": "t1"})
    log.append("2026-11-07T00:00:01Z", "task_done", {"task_id": "t2"})
    replayed = list(log.lines)  # 模拟 I-003 游标重灌：全史重追加
    broken = DecisionLog(list(log.lines) + replayed)
    assert not broken.verify_chain()
    rebuilt, dropped = plan_repair(broken.lines)
    assert dropped == 2 and len(rebuilt) == 2
    assert DecisionLog(rebuilt).verify_chain()
    assert [x.payload["task_id"] for x in rebuilt] == ["t1", "t2"]  # 保首行、payload 不动


@_case("state.store.append_lines_cache")
def store_append_lines_and_cache():

    from stock_trading_agent.state.store import (
        atomic_append_lines,
        read_json_cached,
        read_jsonl_lines_cached,
    )

    d = Path(tempfile.mkdtemp())
    target = d / "audit" / "x.jsonl"
    atomic_append_lines(target, ["a", "b"])
    atomic_append_lines(target, ["c"])
    assert target.read_text(encoding="utf-8") == "a\nb\nc\n"
    atomic_append_lines(target, [])  # 空列表零 IO
    assert target.read_text(encoding="utf-8") == "a\nb\nc\n"
    j = d / "hot.json"
    j.write_text('{"v": 1}', encoding="utf-8")
    assert read_json_cached(j) == {"v": 1}
    assert read_json_cached(j) == {"v": 1}  # 命中缓存
    j.write_text('{"v": 2}', encoding="utf-8")

    os.utime(j, ns=(j.stat().st_mtime_ns + 1_000_000, 0))  # mtime 推进兜底
    assert read_json_cached(j) == {"v": 2}  # 失效重读
    jl = d / "hot.jsonl"
    jl.write_text('{"n": 1}\n', encoding="utf-8")
    assert len(read_jsonl_lines_cached(jl)) == 1
    with jl.open("a", encoding="utf-8") as fh:
        fh.write('{"n": 2}\n')
    os.utime(jl, ns=(jl.stat().st_mtime_ns + 1_000_000, 0))
    assert len(read_jsonl_lines_cached(jl)) == 2  # 追加后失效重读


@_case("state.prune.plans")
def prune_plans():
    from stock_trading_agent.state.prune import (
        budget_daily_split,
        split_decisions_rows,
        split_queue,
        split_sessions,
    )

    old = {"task_id": "a", "status": "done", "finished_at": "2026-09-01T10:00:00+00:00"}
    fresh = {"task_id": "b", "status": "done", "finished_at": "2026-11-08T10:00:00+00:00"}
    active = {"task_id": "c", "status": "queued", "enqueued_at": "2026-11-08T01:00:00+00:00"}
    keep, arch = split_queue([old, fresh, active], today="2026-11-08")
    assert keep == [fresh, active] and arch == [old]  # 旧终态归档、非终态恒留
    capped_keep, capped_arch = split_queue(
        [
            {"task_id": f"t{i}", "status": "done", "finished_at": f"2026-11-{(i % 8) + 1:02d}"}
            for i in range(600)
        ],
        today="2026-11-08",
    )
    assert len(capped_arch) == 100 and len(capped_keep) == 500  # 超 500 条上限归档
    keep_s, arch_s = split_sessions(
        {
            "u1": {"session_uuid": "u1", "ts": "2026-08-01T00:00:00+00:00"},
            "u2": {"session_uuid": "u2", "ts": "2026-11-08T00:00:00+00:00"},
        },
        today="2026-11-08",
    )
    assert set(keep_s) == {"u2"} and len(arch_s) == 1
    keep_d, arch_d = budget_daily_split(
        {
            "daily": {"2026-04-01": {"usd": 1.0}, "2026-11-08": {"usd": 2.0}},
            "monthly": {"2026-11": {}},
        },
        today="2026-11-08",
    )
    assert set(keep_d) == {"2026-11-08"} and set(arch_d) == {"2026-04-01"}
    rows = [
        '{"seq":1,"ts":"2026-10-31T23:00:00+00:00","kind":"task_done","payload":{}}',
        '{"seq":2,"ts":"2026-11-01T23:00:00+00:00","kind":"task_done","payload":{}}',
        '{"seq":3,"ts":"2026-09-30T23:00:00+00:00","kind":"task_done","payload":{}}',
        '{"broken',
    ]
    keep_r, arch_r = split_decisions_rows(rows, today="2026-11-08")
    # 10-31 距今 8 天：月前缀 < 当月但在尾随保底窗内 → 恒留（外仓 T+5 回看窗覆盖）；
    # 09-30 超窗归档；不可解析行随归档迁出（防 _rebuild 中途崩溃）。
    assert keep_r == [rows[0], rows[1]] and arch_r == [rows[2], rows[3]]


@_case("loop.dirwatch.skip")
def dirwatch_skip_predicate():

    from stock_trading_agent.loop.dirwatch import DirWatcher

    d = Path(tempfile.mkdtemp())
    (d / "ticks").mkdir()
    (d / "ticks" / "t.jsonl").write_text("x", encoding="utf-8")
    (d / "queue.json").write_text("{}", encoding="utf-8")
    ticks = d / "ticks"
    watcher = DirWatcher(
        roots=(d,),
        on_change=lambda _: None,
        to_event=lambda p: "queue" if p.name == "queue.json" else None,
        skip=lambda p: p.is_relative_to(ticks),
    )
    watcher.poll_once()  # 建基线
    (d / "ticks" / "t.jsonl").write_text("y", encoding="utf-8")  # 被跳过子树变更
    assert watcher.poll_once() == set()
    (d / "queue.json").write_text('{"v":1}', encoding="utf-8")
    assert watcher.poll_once() == {"queue"}  # 非跳过路径照常捕获


@_case("loop.watcher.conditional_save")
def watcher_conditional_save():

    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.loop.watcher import EventsWatcher
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout(root=Path(tempfile.mkdtemp()))
    ev_file = layout.monitor_events_file(market_today())
    ev_file.parent.mkdir(parents=True, exist_ok=True)
    ev_file.write_text("", encoding="utf-8")
    w = EventsWatcher(layout=layout, on_events=lambda _: None)
    w.poll_once()  # 建基线（首写）
    state = layout.watcher_state_path
    first_mtime = state.stat().st_mtime_ns
    w.poll_once()  # 偏移零变化（W6：不重写）
    assert state.stat().st_mtime_ns == first_mtime

    def _ev_line(ts: str) -> str:
        return json.dumps(
            {
                "ts": ts,
                "code": "600519",
                "market": "cn",
                "kind": "real",
                "rule": "stop_break",
                "level": "T0",
                "notify": True,
                "review": "止损复核",
            },
            ensure_ascii=False,
        )

    with ev_file.open("a", encoding="utf-8") as fh:  # 偏移推进 → 必须重写（别名回归钉）
        fh.write(_ev_line("09:31:00") + "\n")
    assert len(w.poll_once()) == 1
    assert state.stat().st_mtime_ns != first_mtime
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["offsets"] == {ev_file.name: len(_ev_line("09:31:00").encode("utf-8")) + 1}
    # 偏移快照须与内存解耦：再推进后不落盘的比较不得因别名恒等而漏写
    with ev_file.open("a", encoding="utf-8") as fh:
        fh.write(_ev_line("09:32:00") + "\n")
    assert len(w.poll_once()) == 1
    saved2 = json.loads(state.read_text(encoding="utf-8"))
    assert saved2["offsets"][ev_file.name] > saved["offsets"][ev_file.name]


@_case("ops.state_probe.report")
def state_probe_report():

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
    import state_probe

    metrics = {
        "asof": "2026-11-08",
        "root": "/tmp/x",
        "decisions_lines": 57,
        "decisions_bytes": 21_000,
        "events_today_lines": 12,
        "queue_tasks": 5,
        "sources": {
            "reports_md": {"files": 101, "bytes": 1_000_000},
            "reports_scans": {"files": 20, "bytes": 8_100_000},
            "monitor_ticks": {"files": 4, "bytes": 1_800_000},
            "audit": {"files": 4, "bytes": 1_024},
        },
        "total": {"files": 451, "bytes": 12_000_000},
    }
    text = state_probe.format_text(metrics)
    assert "decisions 57 行" in text and "451 文件" in text
    status = state_probe.trigger_status(metrics)
    assert status["T1_decisions_scale"]["hit"] is False
    assert status["T3_file_count"]["hit"] is False


@_case("loop.advice.review_lookup")
def advice_review_lookup():
    from stock_trading_agent.loop.advice import has_review

    lines = [
        {"kind": "advice_reviewed", "payload": {"date": "2026-11-09", "code": "600519"}},
        {"kind": "briefing_ack", "payload": {"date": "2026-11-09", "code": "600519"}},
    ]
    assert has_review(lines, date="2026-11-09", code="600519") is True
    assert has_review(lines, date="2026-11-08", code="600519") is False


@_case("server.panel.price_merge")
def price_merge_cases():
    from stock_trading_agent.server.panel_data import (
        PriceFact,
        asof_stale,
        merge_price_facts,
        parse_ticks_prices,
        t1_price,
    )

    merged = merge_price_facts(
        {"A": PriceFact(9.0, "2026-11-09T09:00:00+08:00", "sim")},
        {"A": PriceFact(9.6, "2026-11-09 10:05:00", "ticks")},  # 空间隔形态新者胜
    )
    assert merged["A"].price == 9.6 and merged["A"].source == "ticks"
    merged2 = merge_price_facts({"A": PriceFact(9.0, "", "sim")}, {"A": PriceFact(9.1, "", "x")})
    assert merged2["A"].price == 9.1  # 双无 asof 后到覆盖
    ticks = parse_ticks_prices(
        ['{"code":"600519","price":9.4}', '{"code":"600519","price":9.6}', "{bad"]
    )
    assert ticks["600519"].price == 9.6  # 尾扫后行覆盖、坏行跳过
    assert t1_price({"blocks": {"quote": {"price": 9.5, "state": "available"}}}) is not None
    assert t1_price({"blocks": {"quote": {"price": 9.5, "state": "stale"}}}) is None
    assert asof_stale("2026-11-08 15:00:00", "2026-11-09") is True
    assert asof_stale("2026-11-09T01:00:00+00:00", "2026-11-09") is False


@_case("server.panel.phase_lunch")
def phase_lunch_matrix():
    from stock_trading_agent.loop.events import MARKET_TZ
    from stock_trading_agent.server.panel_data import market_phase

    def at(h: int, m: int) -> dt.datetime:
        return dt.datetime(2026, 11, 9, h, m, tzinfo=MARKET_TZ)  # 周一

    assert market_phase(at(12, 0))["overall"] == "lunch"  # 双市同休显午休
    assert market_phase(at(11, 45))["overall"] == "live"  # CN 午休 HK 盘中 → 盘中
    assert market_phase(at(13, 0))["overall"] == "live"  # 端点不含复牌


@_case("cc_client.upgrade_gate.diff")
def upgrade_gate_diff():
    from stock_trading_agent.cc_client.upgrade_gate import (
        Finding,
        aggregate,
        diff_snapshots,
        scan_changelog,
        snapshot_from_capture,
    )

    help_text = (
        "Options:\n"
        "  --permission-mode <mode>              Permission mode (choices:\n"
        '                                        "acceptEdits", "dontAsk")\n'
        "  --zz-example <x>                      Example non-watched\n"
    )
    base = snapshot_from_capture("2.1.319", "t", help_text)
    broken = help_text.replace("  --zz-example <x>                      Example non-watched\n", "")
    findings = diff_snapshots(base, snapshot_from_capture("2.1.320", "t", broken))
    assert any(f.code == "flag_removed" for f in findings)  # 非在用消失=warn
    assert aggregate([Finding("x", "fail", "y")]).verdict == "fail"
    hits = scan_changelog(
        "# C\n## 2.1.320\n- removed legacy flag\n## 2.1.319\n- stable\n",
        "2.1.319",
        "2.1.320",
    )
    assert any(f.code == "changelog_keyword" for f in hits)


@_case("ops.upgrade-gate.baseline")
def upgrade_gate_baseline_present():

    gate_dir = Path(__file__).resolve().parents[2] / "ops" / "upgrade-gate"
    pinned = json.loads((gate_dir / "pinned.json").read_text(encoding="utf-8"))
    ver = pinned["cli_version"]
    assert (gate_dir / f"claude-{ver}.snapshot.json").exists(), f"基线快照缺席：{ver}"
    from stock_trading_agent.cc_client.upgrade_gate import load_snapshot, watch_flags

    snap = load_snapshot(gate_dir / f"claude-{ver}.snapshot.json")
    # 防基线腐化：在用旗标除隐藏面（--max-turns 实测缺席）外必须在基线可见
    hidden = {"--max-turns"}
    missing = [f for f in watch_flags() - hidden if f not in snap.flags]
    assert not missing, f"基线缺在用旗标：{missing}"
