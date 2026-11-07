"""CLI 入口（argparse，沿家族脚本风格）：serve / probe / selftest 三子命令。

selftest 与 pytest 同源判据（AGENTS.md 测试门禁节：全绿才可提交，判据只收严）。
"""

from __future__ import annotations

import argparse
import sys


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from stock_trading_agent.server.app import HOST, PORT, create_app

    uvicorn.run(create_app(), host=HOST, port=args.port or PORT, workers=1, log_level="info")
    return 0


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
    serve_p = sub.add_parser("serve", help="起本地面板（FastAPI + 静态页，默认 dry-run 通道）")
    serve_p.add_argument("--port", type=int, default=None)
    sub.add_parser("probe", help="CC 通道探针（骨架期即 dry-run 报告）")
    sub.add_parser("selftest", help="聚合纯函数回归（与 pytest 同源判据）")
    args = parser.parse_args(argv)
    handlers = {"serve": _cmd_serve, "probe": _cmd_probe, "selftest": _cmd_selftest}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
