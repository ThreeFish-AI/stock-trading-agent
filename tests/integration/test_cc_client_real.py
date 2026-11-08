"""真实 CC 通道集成：fake claude 可执行族（PATH 注入 + STA_ALLOW_REAL_CC=1，零网络零本机 CLI）。

真跑用例以 STA_IT_REAL=1 环境门标记（AGENTS.md CI 约定；CI 无该 env 天然跳过，仅本机手跑）。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from stock_trading_agent.budget.gate import billing_mode_from_probe
from stock_trading_agent.cc_client.cli import HeadlessCLIClient, ProbeError, RealSpawnDisabled
from stock_trading_agent.cc_client.contract import CCRequest
from stock_trading_agent.loop.assertor import assess_result

INIT = (
    '{"type":"system","subtype":"init","session_id":"sid-1","capabilities":{},'
    '"mcp_servers":[],"mcp_server_errors":[]}'
)
RESULT_OK = (
    '{"type":"result","subtype":"success","session_id":"sid-1","total_cost_usd":0.01,'
    '"usage":{"input_tokens":10,"output_tokens":5},'
    '"structured_output":{"kind":"diagnose_quick","signal":"hold","rationale":"r",'
    '"disclaimer":"AI 生成，用户复核前置"},"transcript_path":"/tmp/none.jsonl"}'
)


def make_client(
    tmp_path: Path, *, body: str, version: str = "2.1.310 (Claude Code)"
) -> HeadlessCLIClient:
    """fake claude：打印 body 行后按需退出版本分支；PATH 前插 + 门 env 注入。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n{body}\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["STA_ALLOW_REAL_CC"] = "1"
    return HeadlessCLIClient(
        audit_dir=tmp_path / "audit", env=env, home=tmp_path / "home", cli_bin="claude"
    )


def make_request(**kw) -> CCRequest:
    base = dict(
        task_id="diagnose_quick:t",
        session_uuid="00000000-0000-4000-8000-000000000001",
        prompt="/vibe-stock 600519",
        schema=None,
        budget_usd=5.0,
        max_turns=20,
        allowed_tools=("Read", "Glob", "Grep", "Bash"),
    )
    base.update(kw)
    return CCRequest(**base)


@pytest.fixture(autouse=True)
def _gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STA_ALLOW_REAL_CC", "1")


class TestSpawn:
    def test_normal_flow_full_criteria(self, tmp_path: Path):
        client = make_client(tmp_path, body=f"echo '{INIT}'; echo '{RESULT_OK}'")
        result = client.spawn_once(make_request())
        assert result.exit_code == 0
        assert result.envelope["subtype"] == "success"
        assert result.structured_output["signal"] == "hold"
        assert result.result_line_present
        assert result.usage_tokens == {"input_tokens": 10, "output_tokens": 5}
        assert result.mcp_server_errors == []
        verdict = assess_result(result, "diagnose_quick")
        assert verdict.ok, verdict.reasons

    def test_missing_result_line(self, tmp_path: Path):
        client = make_client(tmp_path, body=f"echo '{INIT}'")
        result = client.spawn_once(make_request())
        assert not result.result_line_present and result.envelope is None
        assert not assess_result(result, "diagnose_quick").ok

    def test_error_subtype_refused(self, tmp_path: Path):
        bad = (
            '{"type":"result","subtype":"error_max_budget_usd",'
            '"session_id":"s","total_cost_usd":1.0}'
        )
        client = make_client(tmp_path, body=f"echo '{INIT}'; echo '{bad}'")
        result = client.spawn_once(make_request())
        verdict = assess_result(result, "diagnose_quick")
        assert not verdict.ok and any("subtype 非 success" in r for r in verdict.reasons)

    def test_exit_143_external_sigterm(self, tmp_path: Path):
        client = make_client(tmp_path, body=f"echo '{INIT}'; exit 143")
        result = client.spawn_once(make_request())
        assert result.exit_code == 143
        assert any("143" in r for r in assess_result(result, "diagnose_quick").reasons)

    def test_timeout_sigint_collects_turn(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import stock_trading_agent.cc_client.cli as cli

        monkeypatch.setattr(cli, "SIGINT_GRACE_S", 1.0)
        body = f"echo '{INIT}'; trap 'exit 130' INT; sleep 30"
        client = make_client(tmp_path, body=body)
        result = client.spawn_once(make_request(timeout_s=1))
        assert result.exit_code == 130  # SIGINT 收轮成功（归一化）
        assert not assess_result(result, "diagnose_quick").ok

    def test_timeout_sigkill_escalation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import time

        import stock_trading_agent.cc_client.cli as cli

        monkeypatch.setattr(cli, "SIGINT_GRACE_S", 1.0)
        body = f"echo '{INIT}'; trap '' INT; sleep 30"
        client = make_client(tmp_path, body=body)
        start = time.monotonic()
        result = client.spawn_once(make_request(timeout_s=1))
        assert result.exit_code == 137  # SIGINT 无效 → SIGKILL 升级
        assert time.monotonic() - start < 15

    def test_mcp_server_errors_refused(self, tmp_path: Path):
        bad_init = INIT.replace('"mcp_server_errors":[]', '"mcp_server_errors":[{"server":"x"}]')
        client = make_client(tmp_path, body=f"echo '{bad_init}'; echo '{RESULT_OK}'")
        result = client.spawn_once(make_request())
        verdict = assess_result(result, "diagnose_quick")
        assert not verdict.ok and any("F-23" in r for r in verdict.reasons)

    def test_cwd_injected(self, tmp_path: Path):
        seen = tmp_path / "cwd.txt"
        body = f"pwd > {seen}; echo '{INIT}'; echo '{RESULT_OK}'"
        client = make_client(tmp_path, body=body)
        workdir = tmp_path / "runs" / "sess"
        workdir.mkdir(parents=True)
        client.spawn_once(make_request(cwd=str(workdir)))
        assert seen.read_text(encoding="utf-8").strip() == str(workdir)

    def test_env_sanitized_for_child(self, tmp_path: Path):
        out = tmp_path / "env.txt"
        body = f"env | sort > {out}; echo '{INIT}'; echo '{RESULT_OK}'"
        env = dict(os.environ)
        env["ANTHROPIC_API_KEY"] = "x"  # 测试注入位（R2 扫描只覆盖 src/）
        env["STA_ALLOW_REAL_CC"] = "1"
        bin_dir = tmp_path / "bin2"
        bin_dir.mkdir()
        script = bin_dir / "claude"
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        client = HeadlessCLIClient(env=env, home=tmp_path / "h")
        client.spawn_once(make_request())
        child_env = out.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in child_env  # 剥离（F-31）
        assert "DISABLE_AUTOUPDATER=1" in child_env  # 版本钉死（§14）

    def test_resume_passes_flag_and_full_argv(self, tmp_path: Path):
        seen = tmp_path / "argv.txt"
        body = f"printf \"%s\\n\" \"$@\" > {seen}; echo '{INIT}'; echo '{RESULT_OK}'"
        client = make_client(tmp_path, body=body)
        client.resume("sid-9", make_request())
        argv = seen.read_text(encoding="utf-8").splitlines()
        assert "--resume" in argv and argv[argv.index("--resume") + 1] == "sid-9"
        for flag in (
            "--session-id",
            "--max-budget-usd",
            "--max-turns",
            "--permission-mode",
            "--allowedTools",
            "--strict-mcp-config",
            "--system-prompt-snapshot",
        ):
            assert flag in argv, flag  # D-32 旗标全量重传


class TestArchive:
    def test_archive_after_gate_only(self, tmp_path: Path):
        client = make_client(tmp_path, body=f"echo '{INIT}'; echo '{RESULT_OK}'")
        req = make_request()
        result = client.spawn_once(req)
        files = list((tmp_path / "audit").rglob("*.jsonl")) if (tmp_path / "audit").exists() else []
        assert not files  # NR-3：spawn 期不落盘
        assert assess_result(result, "diagnose_quick").ok
        client.archive(req)
        archived = list((tmp_path / "audit").rglob("*.jsonl"))
        assert len(archived) == 1
        lines = archived[0].read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["type"] == "system"
        assert json.loads(lines[-1])["type"] == "result"


class TestProbe:
    def _client(self, tmp_path: Path, *, with_key: bool, with_credentials: bool):
        env = {k: v for k, v in os.environ.items() if k != "PATH"}
        env["PATH"] = os.environ.get("PATH", "")
        if with_key:
            env["ANTHROPIC_API_KEY"] = "x"
        home = tmp_path / ("h1" if with_credentials else "h2")
        if with_credentials:
            (home / ".claude").mkdir(parents=True, exist_ok=True)
            (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
        bin_dir = tmp_path / "binp"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "claude"
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "2.1.310 (Claude Code)"; exit 0; fi\n'
            "exit 64\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        return HeadlessCLIClient(env=env, home=home)

    def test_version_and_auth_quadrant(self, tmp_path: Path):
        r = self._client(tmp_path, with_key=False, with_credentials=True).probe()
        assert r.cli_version.startswith("2.1.310") and r.auth_mode == "subscription"
        assert billing_mode_from_probe(r.auth_mode) == "subscription"
        r2 = self._client(tmp_path, with_key=True, with_credentials=True).probe()
        assert r2.auth_mode == "api"  # 密钥名在场即 api（F-31）
        r3 = self._client(tmp_path, with_key=False, with_credentials=False).probe()
        assert r3.auth_mode == "api"  # 判不准收紧（D2）
        assert billing_mode_from_probe("dry-run") == "api"

    def test_missing_cli_raises_probe_error(self, tmp_path: Path):
        env = dict(os.environ)
        env["PATH"] = str(tmp_path / "empty")
        (tmp_path / "empty").mkdir()
        env["STA_ALLOW_REAL_CC"] = "1"
        client = HeadlessCLIClient(env=env, home=tmp_path / "h")
        with pytest.raises(ProbeError):
            client.probe()


class TestGate:
    def test_default_env_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("STA_ALLOW_REAL_CC", raising=False)
        client = HeadlessCLIClient(home=tmp_path)
        with pytest.raises(RealSpawnDisabled):
            client.spawn_once(make_request())


class TestRealRun:
    """STA_IT_REAL=1 本机手跑：真 probe + 一次最小 spawn（订阅额度约 $0.2–0.65）。"""

    def test_real_probe_and_minimal_spawn(self):
        import os

        if os.environ.get("STA_IT_REAL") != "1":
            pytest.skip("STA_IT_REAL!=1（真实通道用例仅本机手跑，CI 显式跳过）")
        import uuid

        assert os.environ.get("STA_ALLOW_REAL_CC") == "1"
        client = HeadlessCLIClient()
        report = client.probe()
        assert report.cli_version and report.auth_mode in {"subscription", "api"}
        req = CCRequest(
            task_id="probe:it",
            session_uuid=str(uuid.uuid4()),  # 每次新鲜：--session-id 复用已存在会话（实测坑）
            prompt=(
                "请直接给出结构化结果：reply 填「收到」，disclaimer 填"
                "「AI 生成，用户复核前置」。不要使用任何工具。"
            ),
            schema={  # 最小 draft-07（断言器要求全部产物带免责段）
                "type": "object",
                "properties": {
                    "reply": {"type": "string"},
                    "disclaimer": {"type": "string"},
                },
                "required": ["reply", "disclaimer"],
            },
            budget_usd=0.5,
            max_turns=6,  # 2 轮实测 error_max_turns（模型先内部推理一轮）；6 轮稳过
            allowed_tools=("Read",),
            timeout_s=120,
        )
        result = client.spawn_once(req)
        verdict = assess_result(result, "briefing_review")
        assert verdict.ok, verdict.reasons
