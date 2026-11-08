"""CLI 入口（argparse，沿家族脚本风格）：serve / probe / selftest 三子命令。

selftest 与 pytest 同源判据（AGENTS.md 测试门禁节：全绿才可提交，判据只收严）。
"""

from __future__ import annotations

import argparse
import sys


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from stock_trading_agent.server.app import (
        HOST,
        PORT,
        create_app,
        listener_pid,
        port_is_listening,
        probe_panel,
    )

    port = args.port or PORT
    if port_is_listening(HOST, port):  # 端口冲突预检：替代裸 errno 48
        info = probe_panel(HOST, port)
        pid = listener_pid(port)
        if info is not None:  # 本面板已在跑：幂等退出（退出码不影响 KeepAlive 节流重启）
            ver = info.get("version", "?")
            print(f"[sta] 面板已在运行：http://{HOST}:{port}/")
            print(f"[sta] version={ver}，pid={pid or '未知'}")
            print("[sta] 面板在线即目标达成，本次幂等退出（exit 0）；重启面板：")
            print("      launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent")
            if pid:
                print(f"      或 kill {pid}（launchd KeepAlive 30s 内自动重拉）")
            return 0
        print(f"[sta] 端口 {HOST}:{port} 被未知进程占用（pid={pid or '未知'}），面板无法启动。")
        print(f"[sta] 排查：lsof -nP -iTCP:{port} -sTCP:LISTEN；停占口者或换 --port 后重试。")
        return 1
    # enable_loop_runtime：lifespan 起 worker/watcher 线程；daemon 锁被持有时面板降级只读
    uvicorn.run(
        create_app(enable_loop_runtime=True),
        host=HOST,
        port=port,
        workers=1,
        log_level="info",
        timeout_graceful_shutdown=args.graceful_shutdown,
    )
    return 0


def _cmd_run_once(args: argparse.Namespace) -> int:
    """消费恰一个任务（CLI 黑盒消费冒烟/运维手动补跑）。

    退出码：0=done · 1=failed/被拒 · 2=队空或全部 defer · 3=daemon 锁被持有。
    """
    import json
    import os
    from dataclasses import asdict

    from stock_trading_agent.cc_client.cli import HeadlessCLIClient
    from stock_trading_agent.cc_client.dryrun import DryRunCCClient
    from stock_trading_agent.loop.loop import LoopReject
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    # 通道选择同 probe：STA_ALLOW_REAL_CC=1 → 真实 headless 通道（默认 dry-run 安全）
    layout = StateLayout.from_env()
    real = os.environ.get("STA_ALLOW_REAL_CC") == "1"
    cc = (
        HeadlessCLIClient(audit_dir=layout.audit_dir)
        if real
        else DryRunCCClient(audit_dir=layout.audit_dir)
    )
    runtime = LoopRuntime(layout=layout, cc=cc)
    if not runtime.acquire_daemon(timeout_s=1.0):
        print("daemon.lock 被持有（另一消费者在跑：serve 或另一 run-once），exit 3")
        return 3
    try:
        runtime.recover_orphans()
        runtime.apply_l2_config(probe=False)  # L2 联动（读存值；R1）
        if args.kind and args.code:
            try:
                runtime.submit(kind=args.kind, code=args.code, source="cli")
            except LoopReject as exc:
                print(f"提交被拒：{exc}")
                return 1
        task = runtime.consume_one()
    finally:
        runtime.stop()
    if task is None:
        print("队空或全部 defer（锁/禁跑窗），exit 2")
        return 2
    print(json.dumps(asdict(task), ensure_ascii=False))
    return 0 if task.status == "done" else 1


def _cmd_probe(_args: argparse.Namespace) -> int:
    import os

    from stock_trading_agent.budget.gate import billing_mode_from_probe
    from stock_trading_agent.cc_client.cli import HeadlessCLIClient, ProbeError
    from stock_trading_agent.cc_client.dryrun import DryRunCCClient
    from stock_trading_agent.state.paths import StateLayout

    if os.environ.get("STA_ALLOW_REAL_CC") == "1":
        try:
            report = HeadlessCLIClient().probe()
        except ProbeError as exc:
            print(f"probe 失败：{exc}")
            return 1
    else:
        client = DryRunCCClient(audit_dir=StateLayout.from_env().audit_dir)
        report = client.probe()
    caps = ", ".join(f"{k}={v}" for k, v in report.capabilities.items()) or "-"
    mode = billing_mode_from_probe(report.auth_mode)
    print(f"cli_version={report.cli_version} auth_mode={report.auth_mode} capabilities={caps}")
    print(f"billing_mode={mode}（probe 联动，判不准收紧为 api；真实通道需 STA_ALLOW_REAL_CC=1）")
    return 0


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def _run_selftests() -> list[tuple[str, bool, str]]:
    """聚合纯函数回归（与 pytest 同源判据）。返回 [(名, 通过, 摘要)]。"""
    import datetime as dt

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
    from stock_trading_agent.loop.router import load_default_table
    from stock_trading_agent.paper.batches import derive_position_status
    from stock_trading_agent.paper.fills import Bar, match_limit_buy, t1_gap_fallback_limit
    from stock_trading_agent.state.decisions import DecisionLog
    from stock_trading_agent.state.queue import QueueError, QueueState, TaskRecord

    results: list[tuple[str, bool, str]] = []

    def check_case(name: str, fn) -> None:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:
            results.append((name, False, str(exc)))

    def uuid_deterministic():
        a = derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
        b = derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
        assert a == b and a != derive_session_uuid("2026-11-07", "600519", "diagnose_full")

    check_case("cc_client.contract.uuidv5", uuid_deterministic)

    def nr4_gate():
        req = CCRequest(
            task_id="t1",
            session_uuid=derive_session_uuid("d", "c", "k"),
            prompt="/vibe-stock 600519",
            schema=None,
            budget_usd=1.0,
            max_turns=10,
            allowed_tools=("Read",),
        )
        validate_request(req)
        for bad in (0.0, -1.0):
            try:
                validate_request(
                    CCRequest(
                        task_id="t1",
                        session_uuid="u",
                        prompt="p",
                        schema=None,
                        budget_usd=bad,
                        max_turns=10,
                        allowed_tools=("Read",),
                    )
                )
                raise AssertionError("NR-4 未拦截 budget_usd<=0")
            except ContractError:
                pass

    check_case("cc_client.contract.nr4", nr4_gate)

    def argv_snapshot():
        req = CCRequest(
            task_id="t",
            session_uuid="u",
            prompt="/vibe-stock 600519",
            schema=None,
            budget_usd=5,
            max_turns=40,
            allowed_tools=("Read", "Bash"),
        )
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

    check_case("cc_client.cli.argv", argv_snapshot)

    # env_sanitize 用例见 tests/unit/test_cc_client_contract.py（R2 机器门限定密钥名只在 cli.py）

    def strict_cross():
        bar = Bar(date="2026-11-07", open=10.0, high=10.2, low=9.5, close=9.8)
        assert match_limit_buy("600519", 1, 9.5, bar) is None  # low == limit 不成交（两把尺）
        fill = match_limit_buy("600519", 1, 9.6, bar)
        assert fill is not None and fill.price == 9.6
        open_cap = match_limit_buy("600519", 1, 99.0, bar)
        assert open_cap is not None and open_cap.price == 10.0  # 价 = min(open, limit)

    check_case("paper.fills.strict_cross", strict_cross)

    def position_machine():
        from stock_trading_agent.paper.model import Tranche

        t = [Tranche(leg="T1_open", lots=1, ref_price=10.0)]
        assert derive_position_status(t) == "pending_open"
        assert derive_position_status([t[0].model_copy(update={"status": "filled"})]) == "holding"
        assert derive_position_status([t[0].model_copy(update={"status": "expired"})]) == "closed"

    check_case("paper.batches.position", position_machine)

    def gap_fallback():
        assert t1_gap_fallback_limit(10.0) == 10.2

    check_case("paper.fills.t1_gap", gap_fallback)

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

    check_case("state.queue.idem_wal", queue_idem)

    def hash_chain():
        log = DecisionLog()
        log.append("2026-11-07T00:00:00Z", "task_done", {"task_id": "t"})
        log.append("2026-11-07T00:00:01Z", "task_done", {"task_id": "t2"})
        assert log.verify_chain()
        log.lines[0].payload["task_id"] = "tampered"
        assert not log.verify_chain()

    check_case("state.decisions.hash_chain", hash_chain)

    def routing_table():
        table = load_default_table()
        assert table.route("diagnose_quick") is not None
        assert table.route("nonexistent") is None
        assert table.fallback_count == 1

    check_case("loop.router.table", routing_table)

    def no_report_window():
        from zoneinfo import ZoneInfo

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

    check_case("loop.events.window_lock", no_report_window)

    def assertor_verdicts():
        from pathlib import Path

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

    check_case("loop.assertor.verdicts", assertor_verdicts)

    def budget_gates():
        cfg = BudgetConfig(enabled=True, billing_mode="api")
        assert not check(
            cfg, session_est_usd=2.0, day_spent_usd=0, month_spent_usd=0, day_task_count=0
        ).allowed
        assert not check(
            cfg, session_est_usd=0.5, day_spent_usd=8.0, month_spent_usd=0, day_task_count=0
        ).allowed
        sub = BudgetConfig(enabled=True, billing_mode="subscription")
        v = check(
            sub, session_est_usd=999, day_spent_usd=999, month_spent_usd=9999, day_task_count=0
        )
        assert v.allowed and v.record_only
        led = record_dual_axis({"input_tokens": 10}, 0.5, {}, day="2026-11-08", month="2026-11")
        assert led["tokens"]["input_tokens"] == 10 and led["tasks"] == 1

    check_case("budget.gate.axes", budget_gates)

    def monitor_event_parsing():
        from stock_trading_agent.loop.monitor_events import MonitorEvent, parse_line, t1_trigger

        good = parse_line(
            '{"ts":"09:31:00","code":"600519","market":"cn","kind":"real","rule":"stop_break",'
            '"level":"T0","notify":true,"mech_risk":{"downgrade":true,"severity":"high"},'
            '"facts":{"pct":-2.1},"review":"止损复核"}'
        )
        assert good is not None and good.code == "600519" and good.review == "止损复核"
        assert parse_line("{截断") is None and parse_line("") is None  # 坏行不抛
        assert t1_trigger(good)
        assert not t1_trigger(
            MonitorEvent(
                ts="t",
                code="600519",
                market="cn",
                kind="real",
                rule="r",
                level="T0",
                notify=True,
                suppress_reason="cooldown",
                review="止损复核",
            )
        )
        assert not t1_trigger(
            MonitorEvent(ts="t", code="*", market="cn", kind="*", rule="r", level="T0", notify=True)
        )

    check_case("loop.monitor_events.t1_trigger", monitor_event_parsing)

    def reject_status_map():
        from stock_trading_agent.loop.loop import RejectReason
        from stock_trading_agent.server.schemas import REJECT_STATUS

        assert REJECT_STATUS[RejectReason.DUPLICATE] == 409
        assert REJECT_STATUS[RejectReason.LOCK] == 423
        assert REJECT_STATUS[RejectReason.WINDOW] == 423
        assert REJECT_STATUS[RejectReason.UNKNOWN_KIND] == 422

    check_case("server.schemas.reject_status", reject_status_map)

    def task_eligible_matrix():
        import datetime as dt
        from zoneinfo import ZoneInfo

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

    check_case("loop.worker.task_eligible", task_eligible_matrix)

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

    check_case("cc_client.cli.parse_stream", cc_normalize_and_parse)

    def billing_from_probe():
        from stock_trading_agent.budget.gate import billing_mode_from_probe

        assert billing_mode_from_probe("subscription") == "subscription"
        assert billing_mode_from_probe("api") == "api"
        assert billing_mode_from_probe("dry-run") == "api"  # 判不准收紧（D2）

    check_case("budget.gate.billing_from_probe", billing_from_probe)

    def launchd_plist_pinned():
        import plistlib

        plist_path = _repo_root() / "ops" / "launchd" / "com.threefish.stocktrading.agent.plist"
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
        assert data["KeepAlive"] is True
        assert data["AbandonProcessGroup"] is True
        assert data["ExitTimeOut"] == 20 and data["ThrottleInterval"] == 30
        assert data["RunAtLoad"] is True and "serve" in data["ProgramArguments"]

    check_case("ops.launchd.plist", launchd_plist_pinned)

    def advice_constants_and_scan():
        from stock_trading_agent.loop.advice import (
            ADVICE_ACTIONS,
            render_advice_card,
            scan_advice_text,
        )
        from stock_trading_agent.loop.monitor_events import market_today

        assert ADVICE_ACTIONS == ("可加仓", "持有", "持有·等回踩", "持有观察", "减仓", "离场")
        payload = {
            "outcome": "card",
            "candidate_action": "持有观察",
            "final_signal": "hold",
            "prev_diff": {"prev_final_signal": "none", "note": "n"},
            "t1_sell_pct": 85.0,
            "t1_sell_tier": "大部分",
            "verdict_date": market_today(),
            "opinion_data_date": market_today(),
            "replayed_opinions": 2,
            "trend_ref": {
                "band_rows": [
                    {"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0}
                ]
            },
            "rationale": "r",
        }
        md = render_advice_card(payload, code="600519", date=market_today(), task_id="t")
        assert scan_advice_text(md) == []
        bad = render_advice_card(
            {**payload, "rationale": "卖 1200 股"}, code="600519", date="d", task_id="t"
        )
        assert scan_advice_text(bad)  # 份额原值负断言命中

    check_case("loop.advice.card_scan", advice_constants_and_scan)

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

    check_case("loop.advice.anchor_gate", advice_anchor_and_gate)

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

    check_case("paper.tplus1_band", paper_tplus1_and_band)

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

    check_case("paper.tax_roundlot", paper_tax_and_roundlot)

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

    check_case("paper.equity_baseline", paper_equity_baseline)

    def paper_pending_roundtrip():
        import tempfile
        from pathlib import Path

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

    check_case("paper.pending_roundtrip", paper_pending_roundtrip)

    def sse_event_mapping():
        from pathlib import Path

        from stock_trading_agent.server.stream_hub import to_event_name
        from stock_trading_agent.state.paths import StateLayout

        layout = StateLayout(root=Path("/tmp/sse-test"))
        assert to_event_name(layout.root / "agent" / "queue.json", layout) == "queue"
        assert to_event_name(layout.root / "monitor" / "events" / "d.jsonl", layout) == "monitor"
        assert to_event_name(layout.root / "sim-portfolio.json", layout) == "paper"

    check_case("server.sse.event_map", sse_event_mapping)

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

    check_case("server.stats_briefing.scan", stats_and_briefing_scan)

    def d8_probe_parsers():
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "ops"))
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

    check_case("ops.d8_probe.parsers", d8_probe_parsers)

    def l2_t2_worthy_and_config():
        from stock_trading_agent.loop.monitor_events import MonitorEvent, t2_worthy
        from stock_trading_agent.state.agent_config import AgentConfig, load_agent_config

        ev = MonitorEvent(
            ts="t",
            code="600519",
            market="cn",
            kind="real",
            rule="target_hit",
            level="T0",
            notify=True,
        )
        assert t2_worthy(ev)
        ops = MonitorEvent(
            ts="t",
            code="600519",
            market="cn",
            kind="real",
            rule="storm_digest",
            level="T0",
            notify=True,
        )
        assert not t2_worthy(ops)
        assert AgentConfig().l2_enabled is False  # 安全缺省
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        assert load_agent_config(d / "none.json").l2_enabled is False

    check_case("loop.l2.t2_worthy_config", l2_t2_worthy_and_config)

    def l2_origin_guard():
        from stock_trading_agent.server.origin_guard import origin_allowed

        assert origin_allowed({"origin": "http://127.0.0.1:8730"})
        assert origin_allowed({"origin": "http://testserver"})
        assert not origin_allowed({})
        assert not origin_allowed({"origin": "https://evil.example"})

    check_case("server.origin_guard.allowed", l2_origin_guard)

    def l2_leak_scan():
        from stock_trading_agent.cc_client.leak import find_orphans, parse_pgrep_output

        entries = parse_pgrep_output("123 claude -p x\nbad\n456 claude -p y\n")
        assert entries == [(123, "claude -p x"), (456, "claude -p y")]
        assert find_orphans(entries, {123}) == [(456, "claude -p y")]

    check_case("cc_client.leak.scan", l2_leak_scan)

    def panel_phase_and_trend():
        import datetime as dt
        from zoneinfo import ZoneInfo

        from stock_trading_agent.loop.advice import parse_trend_table, render_advice_card
        from stock_trading_agent.server.panel_data import market_phase

        tz = ZoneInfo("Asia/Shanghai")
        assert market_phase(dt.datetime(2026, 11, 9, 10, 0, tzinfo=tz))["overall"] == "live"
        assert market_phase(dt.datetime(2026, 11, 8, 10, 0, tzinfo=tz))["overall"] == "closed"
        payload = {
            "outcome": "card",
            "candidate_action": "持有观察",
            "final_signal": "hold",
            "prev_diff": {"prev_final_signal": "none", "note": "n"},
            "t1_sell_pct": 85.0,
            "t1_sell_tier": "b",
            "verdict_date": "d",
            "opinion_data_date": "d",
            "replayed_opinions": 2,
            "trend_ref": {
                "band_rows": [
                    {"horizon": "1d", "low": 9.5, "high": 10.5, "prob_touch_low_pct": 31.0}
                ]
            },
            "rationale": "r",
        }
        md = render_advice_card(payload, code="c", date="d", task_id="t")
        rows = parse_trend_table(md)
        assert rows and rows[0]["low"] == 9.5

    check_case("panel.phase_trend", panel_phase_and_trend)

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

    check_case("state.queue.find_any", queue_find_any)

    def port_guard_probe():
        from stock_trading_agent.server.app import _is_panel_health

        assert _is_panel_health({"ok": True, "component": "stock-trading-agent-panel", "v": "x"})
        assert not _is_panel_health({"ok": True, "component": "other-service"})
        assert not _is_panel_health("stock-trading-agent-panel")  # 非 dict 判否

    check_case("server.port_guard.probe", port_guard_probe)

    return results


def _cmd_selftest(_args: argparse.Namespace) -> int:
    results = _run_selftests()
    failed = [r for r in results if not r[1]]
    for name, ok, err in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {err}" if err else ""))
    print(
        f"\n{len(results) - len(failed)}/{len(results)} 全绿"
        if not failed
        else f"\n{len(failed)} 项失败"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stock-trading-agent", description="Stock Trading Agent CLI（骨架）"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser(
        "serve", help="起本地面板（FastAPI + 静态页 + Loop 运行时，默认 dry-run 通道）"
    )
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument(
        "--graceful-shutdown",
        type=int,
        default=10,
        help="uvicorn 优雅关停窗（秒）；须小于 launchd ExitTimeOut（默认 10 < 20，R-12）",
    )
    run_p = sub.add_parser(
        "run-once", help="消费恰一个队列任务（退出码 0/1/2/3；与 serve 抢 daemon 锁）"
    )
    run_p.add_argument("--kind", default=None, help="可选：先提交再消费（六 kind 或 t1_refresh）")
    run_p.add_argument("--code", default=None, help="标的码（与 --kind 成对）")
    sub.add_parser("probe", help="CC 通道探针（骨架期即 dry-run 报告）")
    sub.add_parser("selftest", help="聚合纯函数回归（与 pytest 同源判据）")
    args = parser.parse_args(argv)
    handlers = {
        "serve": _cmd_serve,
        "run-once": _cmd_run_once,
        "probe": _cmd_probe,
        "selftest": _cmd_selftest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
