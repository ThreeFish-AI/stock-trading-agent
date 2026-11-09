"""CLI 入口（argparse，沿家族脚本风格）：serve / probe / selftest 三子命令。

selftest 与 pytest 同源判据（AGENTS.md 测试门禁节：全绿才可提交，判据只收严）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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
        if runtime.chain_error:
            print(f"决策链断裂，拒消费（{runtime.chain_error}），exit 3")
        else:
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


def _cmd_repair_decisions(_args: argparse.Namespace) -> int:
    """一次性修复 decisions.jsonl 断链（保行重链 + 备份不删行；0.0.15 runbook）。

    完好链 noop exit 0；坏 JSON 行即中止人工检查（不删行纪律下无降级路径）。
    修复后建议 launchctl kickstart -k gui/$(id -u)/com.threefish.stocktrading.agent 重启 serve。
    """
    import datetime as dt
    import hashlib
    import json

    from stock_trading_agent.state.decisions import (
        DecisionChainError,
        DecisionLog,
        repair_chain_lines,
    )
    from stock_trading_agent.state.paths import StateLayout
    from stock_trading_agent.state.store import FileLock, atomic_write_text

    layout = StateLayout.from_env()
    path = layout.decisions_path
    if not path.exists():
        print(f"decisions.jsonl 不存在（{path}），无需修复")
        return 0
    try:
        log = DecisionLog.load(path)
    except DecisionChainError as exc:
        print(f"检测到断链：{exc}")
    else:
        print(f"决策链完整（{len(log.lines)} 行），无需修复")
        return 0
    text = path.read_text(encoding="utf-8")
    ts = dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")
    old_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rows, stats = repair_chain_lines(text.splitlines(), ts=ts, old_sha256=old_sha256)
    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    with FileLock(layout.decisions_lock):
        atomic_write_text(backup, text)
        atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(
        f"修复完成：{stats.rows_before} 行保行重链 → {stats.rows_after} 行"
        f"（含 chain_repair_started/done 两条；断点行 {stats.breaks}）"
    )
    print(f"备份：{backup}")
    print(f"新链头：{stats.new_head[:12]}…（建议 launchctl kickstart 重启 serve）")
    return 0


def _cmd_paper_import(args: argparse.Namespace) -> int:
    """allocation.json 导入建仓（vibe-stock batch/portfolio 产物零格式发明，06 Stage D）。

    账本缺席 → 以 allocation budget 起始全新建账；已在账 → 追加（dup code 整批拒）。
    决策 paper_allocation_imported payload 零价格零数量（NR-5：仅 date 与 code 列表）。
    """
    import json as _json

    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.paper import ledger as paper_ledger
    from stock_trading_agent.paper.allocation import (
        AllocationError,
        import_allocation,
        new_portfolio_from_allocation,
        parse_allocation,
    )
    from stock_trading_agent.state.paths import StateLayout
    from stock_trading_agent.state.store import FileLock

    layout = StateLayout.from_env()
    try:
        data = _json.loads(Path(args.file).read_text(encoding="utf-8"))
        rows = parse_allocation(data)
    except (OSError, _json.JSONDecodeError, AllocationError, ValueError) as exc:
        print(f"allocation 解析失败：{exc}")
        return 1
    date = args.date or market_today()
    with FileLock(layout.sim_lock):
        portfolio = paper_ledger.load(layout.sim_portfolio_path)
        try:
            portfolio = (
                new_portfolio_from_allocation(data, opened_date=date)
                if portfolio is None
                else import_allocation(portfolio, rows, opened_date=date)
            )
        except Exception as exc:
            print(f"导入失败（整批拒绝，账本未变）：{exc}")
            return 1
        paper_ledger.persist(portfolio, layout.sim_portfolio_path)
    _cli_decide(
        layout,
        "paper_allocation_imported",
        {"date": date, "codes": [r.code for r in rows]},
    )
    print(f"已导入 {len(rows)} 只（T1/T2/T3 三批建仓；opened_date={date}）")
    for r in rows:
        print(f"  {r.code} [{r.market}] 三批 {r.t1_lots}/{r.t2_lots}/{r.t3_lots}")
    return 0


def _cmd_paper_eod(args: argparse.Namespace) -> int:
    """paper 日终驱动（手动/补跑：ticks 末轮 bar → 触价请求/GTC 到期/权益点）。

    与 serve worker 自动驱动同幂等锚（paper_eod_done/skipped 当日一条）；--date 补跑历史日。
    """
    from stock_trading_agent.loop.paper_eod import run_paper_eod
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout.from_env()
    outcome = run_paper_eod(layout, date=args.date, decide=lambda k, p: _cli_decide(layout, k, p))
    if outcome is None:
        print("无事可做（无账本 / 已跑 / 窗前 / 窗内无 ticks 已留痕）")
        return 0
    print(
        f"date={outcome.date} bars={outcome.bars} requests={outcome.requests} "
        f"expired={outcome.expired} converted={outcome.converted} judged={outcome.judged} "
        f"marked={int(outcome.marked)}"
    )
    if outcome.skipped_codes:
        print(f"  无 ticks 跳过（停牌/未入盯盘宇宙）：{','.join(outcome.skipped_codes)}")
    return 0


def _cli_decide(layout, kind: str, payload: dict) -> None:
    """CLI 决策留痕（DecisionLog.append 单一入口；调用序 sim→decisions，§④ 纪律）。"""
    from stock_trading_agent.loop.loop import _now_iso
    from stock_trading_agent.state.decisions import DecisionLog

    DecisionLog().append(_now_iso(), kind, payload, path=layout.decisions_path)


def _cmd_archive_state(args: argparse.Namespace) -> int:
    """六桶状态归档（G1）：默认 dry-run，--apply 落盘（归档非删除，档案 agent/archive/）。

    退出码：0=完成 · 3=daemon 在跑（单写者守卫）。
    """
    from stock_trading_agent.loop.monitor_events import market_today
    from stock_trading_agent.state.paths import StateLayout
    from stock_trading_agent.state.prune import archive_state
    from stock_trading_agent.state.store import FileLock, LockTimeout

    layout = StateLayout.from_env()
    try:
        with FileLock(layout.daemon_lock, timeout_s=1.0):
            summary = archive_state(layout, today=market_today(), apply=args.apply)
            for bucket, stat in summary.items():
                print(f"  {bucket}: 归档 {stat['archive']} / 保留 {stat['keep']}")
            print(
                "已落盘（含 state_archived 留痕）"
                if args.apply
                else "dry-run 未落盘（--apply 执行）"
            )
            return 0
    except LockTimeout:
        print("daemon.lock 被持有（serve/run-once 在跑）——先停守护再归档，exit 3")
        return 3


def _cmd_selftest(_args: argparse.Namespace) -> int:
    from stock_trading_agent.selftest import run_selftests

    results = run_selftests()
    failed = [r for r in results if not r[1]]
    for name, ok, err in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {err}" if err else ""))
    print(
        f"\n{len(results) - len(failed)}/{len(results)} 全绿"
        if not failed
        else f"\n{len(failed)} 项失败"
    )
    return 1 if failed else 0


def _cmd_upgrade_gate(args: argparse.Namespace) -> int:
    """升级门禁三件套（0.0.26，§② 裁定；仅真实通道、显式人工触发）。

    流程见 docs/ops/upgrade-runbook.md。exit 契约：0=PASS · 1=FAIL/错误 · 2=WARN
    （逐条人工核后 --ack-warn 复跑登记）。
    """
    import datetime as dt

    from stock_trading_agent.cc_client.cli import HeadlessCLIClient, ProbeError
    from stock_trading_agent.cc_client.upgrade_gate import (
        Finding,
        aggregate,
        diff_snapshots,
        load_snapshot,
        scan_changelog,
        serialize_snapshot,
        snapshot_from_capture,
    )
    from stock_trading_agent.state.store import atomic_write_text

    gate_dir = (
        Path(args.out).resolve()
        if args.out
        else Path(__file__).resolve().parents[2] / "ops" / "upgrade-gate"
    )
    client = HeadlessCLIClient()

    if args.capture:
        report = client.probe()
        help_text = client.capture_help()
        caps = client.probe(deep=True).capabilities if args.deep else None
        snap = snapshot_from_capture(
            report.cli_version.split()[0],  # "2.1.319 (Claude Code)" → "2.1.319"
            dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
            help_text,
            capabilities=caps or None,
        )
        gate_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            gate_dir / f"claude-{snap.cli_version}.snapshot.json", serialize_snapshot(snap)
        )
        atomic_write_text(gate_dir / f"claude-{snap.cli_version}.help.txt", help_text)
        print(
            f"快照已落 {gate_dir}/claude-{snap.cli_version}.snapshot.json（{len(snap.flags)} 旗标）"
        )
        return 0

    baseline_ver = args.baseline
    if not baseline_ver:
        pinned = gate_dir / "pinned.json"
        if not pinned.exists():
            print(f"基线缺席：{pinned} 不存在——先 --capture 建基线或 --baseline <ver> 指定")
            return 1
        import json

        baseline_ver = str(json.loads(pinned.read_text(encoding="utf-8"))["cli_version"])
    baseline_path = gate_dir / f"claude-{baseline_ver}.snapshot.json"
    if not baseline_path.exists():
        print(f"基线快照缺席：{baseline_path}")
        return 1
    base = load_snapshot(baseline_path)

    findings: list[Finding] = []
    try:
        report = client.probe()
        cur = snapshot_from_capture(
            report.cli_version.split()[0],
            dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
            client.capture_help(),
            capabilities=client.probe(deep=True).capabilities if args.deep else None,
        )
    except ProbeError as exc:
        verdict = aggregate([Finding("version_probe_failed", "fail", str(exc))])
        _upgrade_gate_decide(base.cli_version, "unknown", verdict, args, findings)
        print(f"FAIL  version_probe_failed  {exc}")
        return 1
    if not client.probe_enum_reject():
        findings.append(
            Finding("enum_probe_eaten", "fail", "非法枚举探针 exit 0——隐藏别名吃进（F-30）")
        )
    findings.extend(diff_snapshots(base, cur))
    if args.changelog:
        try:
            text = Path(args.changelog).read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(Finding("changelog_unreadable", "warn", f"CHANGELOG 不可读：{exc}"))
        else:
            findings.extend(scan_changelog(text, base.cli_version, cur.cli_version))
    verdict = aggregate(findings)
    _upgrade_gate_decide(base.cli_version, cur.cli_version, verdict, args, findings)
    for f in verdict.findings:
        print(f"{f.severity.upper():4}  {f.code}  {f.detail}")
    print(f"baseline {base.cli_version} → current {cur.cli_version}：{verdict.verdict.upper()}")
    return {"pass": 0, "warn": 2, "fail": 1}[verdict.verdict]


def _upgrade_gate_decide(baseline_ver: str, cur_ver: str, verdict, args, findings) -> None:
    """upgrade_gate_run 决策留痕（payload 零敏感量，NR-5；断链不静默吞——打指引）。

    坑位：DecisionLog.append 内部已持 decisions.lock——外层不得再包同路径 FileLock
    （不同 fd 的 flock 不认进程内重入，自死锁至 LockTimeout）。
    """
    import datetime as dt

    from stock_trading_agent.state.decisions import DecisionChainError, DecisionLog
    from stock_trading_agent.state.paths import StateLayout

    layout = StateLayout.from_env()
    try:
        DecisionLog().append(
            dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
            "upgrade_gate_run",
            {
                "baseline_version": baseline_ver,
                "current_version": cur_ver,
                "verdict": verdict.verdict,
                "codes": [f.code for f in findings],
                "ack": bool(args.ack_warn),
                "deep": bool(args.deep),
            },
            path=layout.decisions_path,
        )
    except DecisionChainError as exc:
        print(f"决策链断裂，结果照常输出但不落决策——先跑 repair-decisions：{exc}")


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
    import_p = sub.add_parser(
        "paper-import",
        help="allocation.json 导入三批建仓（vibe-stock 批量精选产物；06 Stage D）",
    )
    import_p.add_argument("--file", required=True, help="allocation.json 路径")
    import_p.add_argument("--date", default=None, help="导入日（缺省市场今日）")
    eod_p = sub.add_parser(
        "paper-eod",
        help="paper 日终驱动（ticks 末轮 bar→触价请求/GTC 到期/权益点；--date 补跑）",
    )
    eod_p.add_argument("--date", default=None, help="补跑日（缺省市场今日）")
    sub.add_parser(
        "repair-decisions",
        help="一次性修复 decisions.jsonl 断链（保行重链+备份不删行；strict load 配套）",
    )
    archive_p = sub.add_parser(
        "archive-state",
        help="六桶状态归档（queue/sessions/audit/runs/budget/decisions；默认 dry-run）",
    )
    archive_p.add_argument("--apply", action="store_true", help="执行归档（默认只打印计划）")
    gate_p = sub.add_parser(
        "upgrade-gate",
        help="CLI 升级门禁三件套（--version/--help 快照 diff/非法枚举探针；exit 0/1/2）",
    )
    gate_p.add_argument(
        "--baseline", default=None, help="基线版本（缺省读 ops/upgrade-gate/pinned.json）"
    )
    gate_p.add_argument(
        "--deep", action="store_true", help="追加 deep 探针采 capabilities（真实花钱）"
    )
    gate_p.add_argument(
        "--changelog",
        default=None,
        help="CHANGELOG 文件路径（ops/upgrade_changelog.py 拉取后喂入）",
    )
    gate_p.add_argument("--ack-warn", action="store_true", help="WARN 已逐条人工核过后复跑登记")
    gate_p.add_argument("--json", action="store_true", help="JSON 输出（预留）")
    gate_p.add_argument("--capture", action="store_true", help="采现行快照落盘（不 diff）")
    gate_p.add_argument(
        "--out", default=None, help="快照目录（缺省仓内 ops/upgrade-gate/；测试注入用）"
    )
    sub.add_parser("selftest", help="聚合纯函数回归（与 pytest 同源判据）")
    args = parser.parse_args(argv)
    handlers = {
        "serve": _cmd_serve,
        "run-once": _cmd_run_once,
        "probe": _cmd_probe,
        "paper-import": _cmd_paper_import,
        "paper-eod": _cmd_paper_eod,
        "repair-decisions": _cmd_repair_decisions,
        "archive-state": _cmd_archive_state,
        "upgrade-gate": _cmd_upgrade_gate,
        "selftest": _cmd_selftest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
