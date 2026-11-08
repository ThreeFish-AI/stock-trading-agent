"""CLI 入口（argparse，沿家族脚本风格）：serve / probe / selftest 三子命令。

selftest 与 pytest 同源判据（AGENTS.md 测试门禁节：全绿才可提交，判据只收严）。
"""

from __future__ import annotations

import argparse
import sys


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from stock_trading_agent.server.app import HOST, PORT, create_app

    # enable_loop_runtime：lifespan 起 worker/watcher 线程；daemon 锁被持有时面板降级只读
    uvicorn.run(
        create_app(enable_loop_runtime=True),
        host=HOST,
        port=args.port or PORT,
        workers=1,
        log_level="info",
    )
    return 0


def _cmd_run_once(args: argparse.Namespace) -> int:
    """消费恰一个任务（CLI 黑盒消费冒烟/运维手动补跑）。

    退出码：0=done · 1=failed/被拒 · 2=队空或全部 defer · 3=daemon 锁被持有。
    """
    import json
    from dataclasses import asdict

    from stock_trading_agent.loop.loop import LoopReject
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    runtime = LoopRuntime(layout=StateLayout.from_env())
    if not runtime.acquire_daemon(timeout_s=1.0):
        print("daemon.lock 被持有（另一消费者在跑：serve 或另一 run-once），exit 3")
        return 3
    try:
        runtime.recover_orphans()
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


def _cmd_probe(args: argparse.Namespace) -> int:
    from stock_trading_agent.cc_client.dryrun import DryRunCCClient
    from stock_trading_agent.state.paths import StateLayout

    client = DryRunCCClient(audit_dir=StateLayout.from_env().audit_dir)
    report = client.probe()
    caps = ", ".join(f"{k}={v}" for k, v in report.capabilities.items()) or "-"
    print(f"cli_version={report.cli_version} auth_mode={report.auth_mode} capabilities={caps}")
    print("（骨架期探针即 dry-run 报告；真实通道 STA_ALLOW_REAL_CC=1 且 M1 完整版可用）")
    return 0


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
        led = record_dual_axis({"input_tokens": 10}, 0.5, {})
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
