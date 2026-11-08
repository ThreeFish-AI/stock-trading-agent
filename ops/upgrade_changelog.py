#!/usr/bin/env python3
"""升级门禁 CHANGELOG 拉取（0.0.26；ops/ 网络豁免面，stdlib urllib——d8_probe 先例）。

用法（临时产物纪律：重定向进 .temp/）：
  python3 ops/upgrade_changelog.py > .temp/claude-changelog.md
  STA_ALLOW_REAL_CC=1 uv run stock-trading-agent upgrade-gate --changelog .temp/claude-changelog.md

网络不可得不阻断升级：--changelog 恒支持人工喂入文件；缺项=WARN 人工确认
（12-orchestration §② 升级门禁裁定）。
"""

from __future__ import annotations

import sys
import urllib.request

URL = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
TIMEOUT_S = 10.0
UA = "stock-trading-agent-upgrade-changelog/0.1"


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except OSError as exc:
        print(f"拉取失败：{exc}\n（离线/被墙降级：人工下载 {URL} 喂 --changelog）", file=sys.stderr)
        return 1
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
