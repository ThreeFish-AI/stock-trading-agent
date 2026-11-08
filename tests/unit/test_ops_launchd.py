"""launchd 第四槽模板钉死（plistlib 解析逐键断言，沿 vibe-stock watch.py selftest 先例）。

模板漂移（KeepAlive 形态/AbandonProcessGroup 缺失/ExitTimeOut 倒挂等）在测试层即拦。
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from stock_trading_agent.loop.router import load_default_table

PLIST = Path(__file__).parents[2] / "ops" / "launchd" / "com.threefish.stocktrading.agent.plist"


@pytest.fixture(scope="module")
def parsed() -> dict:
    with PLIST.open("rb") as fh:
        return plistlib.load(fh)


class TestPlistPinned:
    def test_label_and_serve_entry(self, parsed: dict):
        # 直接调 .venv console script（uv run 包装会使 launchd 盯错进程、python 成孤儿占端口）
        assert parsed["Label"] == "com.threefish.stocktrading.agent"
        argv = parsed["ProgramArguments"]
        assert argv[0] == "__REPO_PATH__/.venv/bin/stock-trading-agent"
        assert argv[1:] == ["serve"]

    def test_keepalive_true_and_runatload(self, parsed: dict):
        assert parsed["RunAtLoad"] is True  # 常驻面板随登录自起
        # KeepAlive=true 常驻自愈（实测裁定：Crashed 信号量在本机挂起不触发；停机走 bootout）
        assert parsed["KeepAlive"] is True

    def test_abandon_process_group_true(self, parsed: dict):
        # D3 已裁定：守护重启不连杀在跑 CC 子进程（R-12）
        assert parsed["AbandonProcessGroup"] is True

    def test_exittimeout_window_over_graceful_shutdown(self, parsed: dict):
        # ExitTimeOut(20) > serve --graceful-shutdown 默认 10：关停在杀之前完成（R-12）
        assert parsed["ExitTimeOut"] == 20

    def test_throttle_and_logs(self, parsed: dict):
        assert parsed["ThrottleInterval"] == 30
        assert parsed["StandardOutPath"].endswith(".vibe-stock/agent/logs/panel.out.log")
        assert parsed["StandardErrorPath"].endswith(".vibe-stock/agent/logs/panel.err.log")

    def test_no_watchpaths_no_calendar(self, parsed: dict):
        assert "WatchDirectories" not in parsed and "WatchPaths" not in parsed  # I-52 race-prone
        assert "StartCalendarInterval" not in parsed  # 常驻服务非日历槽（三槽并存不替代）

    def test_template_carries_no_real_cc_env(self, parsed: dict):
        # 出厂模板不带 STA_ALLOW_REAL_CC：dry-run 安全（启用由用户安装时注入，docs/ops/launchd.md）
        assert "EnvironmentVariables" not in parsed

    def test_placeholders_present_for_sed(self):
        raw = PLIST.read_text(encoding="utf-8")
        for token in ("__REPO_PATH__", "__HOME__"):
            assert token in raw


class TestRoutingNail:
    def test_monitor_review_has_nr1_tools(self):
        # NR-1 机器钉：T2 会话须能跑 adjudicate（Bash）与 risk 覆写（Write）——0.0.4 修错防回退
        template = load_default_table().route("monitor_review")
        assert template is not None
        assert {"Bash", "Write"} <= set(template.allowed_tools)
        # 修正集仍是 diagnose_full 工具面的严格子集（无 Edit 等扩权）
        full = load_default_table().route("diagnose_full")
        assert set(template.allowed_tools) < set(full.allowed_tools)
