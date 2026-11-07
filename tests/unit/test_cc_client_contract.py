"""CCRequest 契约：NR-4 类型层闸、UUIDv5 派生、draft-07 收口、旗标快照、env 治理。"""

from __future__ import annotations

import pytest

from stock_trading_agent.cc_client.cli import (
    CLI_BIN,
    RealSpawnDisabled,
    build_argv,
    sanitize_env,
)
from stock_trading_agent.cc_client.contract import (
    CCRequest,
    ContractError,
    derive_session_uuid,
    to_draft07,
    validate_request,
)


def req(**over) -> CCRequest:
    base = dict(
        task_id="t1",
        session_uuid=derive_session_uuid("2026-11-07", "600519", "diagnose_quick"),
        prompt="/vibe-stock 600519",
        schema=None,
        budget_usd=2.0,
        max_turns=20,
        allowed_tools=("Read", "Grep"),
    )
    base.update(over)
    return CCRequest(**base)


class TestUUIDv5:
    def test_deterministic_and_distinct(self):
        a = derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
        assert a == derive_session_uuid("2026-11-07", "600519", "diagnose_quick")
        assert a != derive_session_uuid("2026-11-07", "600519", "diagnose_full")
        assert a != derive_session_uuid("2026-11-08", "600519", "diagnose_quick")


class TestValidate:
    def test_valid_passes(self):
        validate_request(req())

    @pytest.mark.parametrize(
        "over",
        [
            {"budget_usd": 0.0},
            {"budget_usd": -1.0},
            {"max_turns": 0},
            {"max_turns": -5},
            {"task_id": ""},
            {"prompt": "   "},
            {"allowed_tools": ()},
        ],
    )
    def test_nr4_and_presence_gates(self, over):
        with pytest.raises(ContractError):
            validate_request(req(**over))


class TestDraft07:
    def test_none_passthrough(self):
        assert to_draft07(None) is None

    def test_strips_schema_key(self):
        assert "$schema" not in to_draft07(
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
        )

    def test_rejects_2020_12(self):
        with pytest.raises(ContractError, match="2020-12"):
            to_draft07({"$schema": "https://json-schema.org/draft/2020-12/schema"})


class TestBuildArgv:
    def test_flag_surface_snapshot(self):
        argv = build_argv(req())
        assert argv[0] == CLI_BIN
        joined = " ".join(argv)
        for flag in (
            "-p",
            "--output-format stream-json",
            "--session-id",
            "--max-budget-usd 2",
            "--max-turns 20",
            "--permission-mode dontAsk",
            "--allowedTools Read,Grep",
            "--permission-prompts none",
            "--strict-mcp-config",
            "--system-prompt-snapshot off",
        ):
            assert flag in joined, f"缺旗标：{flag}"
        assert argv[-1] == "/vibe-stock 600519"
        assert "--mcp-config" in joined  # strict-empty 双旗标

    def test_resume_mode(self):
        argv = build_argv(req(), resume_session_id="sess-123")
        assert "--resume sess-123" in " ".join(argv)


class TestSanitizeEnv:
    def test_strips_auth_and_pins_updater(self):
        out = sanitize_env(
            {"ANTHROPIC_API_KEY": "secret-value", "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/bin"}
        )
        assert "ANTHROPIC_API_KEY" not in out and "ANTHROPIC_AUTH_TOKEN" not in out
        assert out["PATH"] == "/bin"
        assert out["DISABLE_AUTOUPDATER"] == "1" and out["DISABLE_AUTOUPDATES"] == "1"

    def test_real_spawn_gated(self):
        from stock_trading_agent.cc_client.cli import HeadlessCLIClient

        with pytest.raises(RealSpawnDisabled):
            HeadlessCLIClient().spawn_once(req())
