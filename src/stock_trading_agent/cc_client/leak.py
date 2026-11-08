"""CC 进程泄漏巡检（D-08：2.1.255 无人值守跑完不退出、24h 泄漏 24 进程先例）。

纯函数 + 可注入 runner（测试零子进程）；首版 log-only（决策 cc_orphan_detected 告警），
kill 留 opt-in（坑位：pgrep 误伤面——模式钉 `claude.*-p` 排除交互会话与自身；
live_pids 差集排除在管进程）。
"""

from __future__ import annotations

import subprocess

PGREP_PATTERN = "claude.*-p"


def parse_pgrep_output(text: str) -> list[tuple[int, str]]:
    """`pgrep -fl` 输出 → [(pid, cmdline)]；坏行跳过不炸。"""
    out: list[tuple[int, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmdline = line.partition(" ")
        try:
            out.append((int(pid_str), cmdline.strip()))
        except ValueError:
            continue
    return out


def find_orphans(entries: list[tuple[int, str]], live_pids: set[int]) -> list[tuple[int, str]]:
    """差集：不在 live_pids 管理内的 claude -p 进程（候选孤儿）。"""
    return [(pid, cmd) for pid, cmd in entries if pid not in live_pids]


def scan(
    live_pids: set[int],
    *,
    runner=None,
) -> list[tuple[int, str]]:
    """执行巡检（runner 可注入 subprocess.run 形态函数；默认真跑 pgrep）。"""
    if runner is None:

        def runner(argv: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)

    result = runner(["pgrep", "-fl", PGREP_PATTERN])
    if result.returncode != 0:
        return []  # pgrep 无匹配退出 1；异常态如实零告警（不误报）
    return find_orphans(parse_pgrep_output(result.stdout), live_pids)
