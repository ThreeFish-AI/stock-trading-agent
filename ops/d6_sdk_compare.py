#!/usr/bin/env python3
"""D6 Agent SDK 对照实验脚本（一次性手动实验，不进 pyproject 依赖）。

运行：uv run --with claude-agent-sdk ops/d6_sdk_compare.py --real
（默认无 --real 即安全退出不花钱；环境门 STA_ALLOW_REAL_CC=1 须同场——与真实通道同纪律。）

对照口径（方案 D6/D-16/D-17）：
  - 同 prompt、同 cli_path 钉本机 claude 二进制（SDK 侧 query(..., cli_path=...)）；
  - SDK 侧 preset="claude_code" 对齐 system prompt + setting_sources=[] 隔离；
  - CLI 侧走本仓 build_argv 全旗标面；
  - 各跑 N 次（默认 3），输出 {耗时 p50/max、usage tokens、total_cost_usd、subtype} 对照 JSON。

结果登记：docs/ops/d6-sdk-comparison.md（记录模板随本脚本交付）。
"""

import argparse
import asyncio
import json
import shutil
import statistics
import time


def build_cli_argv(prompt: str, run_idx: int = 0) -> list[str]:
    """每跑新鲜 uuid（12-orchestration §② 坑位③：--session-id 复用已存在会话会接续旧
    上下文；uuid5 按日期派生会在同日重跑时撞「already in use」——uuid4 真新鲜）。"""
    import sys
    import uuid as _uuid
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from stock_trading_agent.cc_client.cli import build_argv
    from stock_trading_agent.cc_client.contract import CCRequest

    req = CCRequest(
        task_id="d6-compare",
        session_uuid=str(_uuid.uuid4()),
        prompt=prompt,
        schema=None,
        budget_usd=0.5,
        max_turns=4,
        allowed_tools=("Read",),
        timeout_s=120,
    )
    return build_argv(req)


def run_cli_once(prompt: str, run_idx: int = 0) -> dict:
    import subprocess

    argv = build_cli_argv(prompt, run_idx)
    start = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=150)
    elapsed = time.monotonic() - start
    envelope = None
    for line in reversed(proc.stdout.splitlines()):
        if '"type": "result"' in line or '"type":"result"' in line:
            try:
                envelope = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    usage = (envelope or {}).get("usage") or {}
    return {
        "elapsed_s": round(elapsed, 2),
        "exit": proc.returncode,
        "subtype": (envelope or {}).get("subtype"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_cost_usd": (envelope or {}).get("total_cost_usd"),
    }


async def run_sdk_once(prompt: str, cli_path: str) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, query

    # API 漂移实证（D6 首跑发现）：claude-agent-sdk 0.2.179 已无 preset/setting_sources
    # 形参（脚本原按调研时点 API 写就）——现版对齐口径=cli_path 钉同二进制 + 默认 system
    # prompt（CLI -p 同源）+ allowed_tools/max_turns/max_budget_usd 同值。
    options = ClaudeAgentOptions(
        cli_path=cli_path,
        allowed_tools=["Read"],
        max_turns=4,
        max_budget_usd=0.5,
    )
    start = time.monotonic()
    subtype = None
    usage: dict = {}
    cost = None
    async for message in query(prompt=prompt, options=options):
        if getattr(message, "subtype", None):
            subtype = message.subtype
        raw = getattr(message, "result", None)
        if isinstance(raw, dict):
            usage = raw.get("usage") or usage
            cost = raw.get("total_cost_usd", cost)
    return {
        "elapsed_s": round(time.monotonic() - start, 2),
        "subtype": subtype,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_cost_usd": cost,
    }


def summarize(runs: list[dict]) -> dict:
    elapsed = [r["elapsed_s"] for r in runs if r.get("elapsed_s") is not None]
    return {
        "n": len(runs),
        "elapsed_p50_s": round(statistics.median(elapsed), 2) if elapsed else None,
        "elapsed_max_s": round(max(elapsed), 2) if elapsed else None,
        "total_cost_usd": round(sum(r.get("total_cost_usd") or 0 for r in runs), 4),
        "runs": runs,
    }


def main() -> int:
    import os

    ap = argparse.ArgumentParser(description="D6 SDK vs CLI 对照实验")
    ap.add_argument("--real", action="store_true", help="真跑（花订阅额度；默认安全退出）")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--prompt", default="只回复两个字：收到")
    args = ap.parse_args()
    if not args.real:
        print("未带 --real：安全退出（真跑请加 --real 且 STA_ALLOW_REAL_CC=1）")
        return 0
    if os.environ.get("STA_ALLOW_REAL_CC") != "1":
        print("STA_ALLOW_REAL_CC!=1：真实通道门未开，拒跑")
        return 1
    cli_path = shutil.which("claude") or str(
        __import__("pathlib").Path.home() / ".local" / "bin" / "claude"
    )
    cli_runs = [run_cli_once(args.prompt, i) for i in range(args.n)]
    sdk_runs = [asyncio.run(run_sdk_once(args.prompt, cli_path)) for _ in range(args.n)]
    report = {"cli": summarize(cli_runs), "sdk": summarize(sdk_runs)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
