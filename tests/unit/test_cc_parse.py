"""HeadlessCLI 纯函数：退出码归一化 + stream-json 解析矩阵（零子进程零网络）。"""

from __future__ import annotations

from stock_trading_agent.cc_client.cli import _parse_stream, normalize_exit_code

INIT = (
    '{"type":"system","subtype":"init","session_id":"sid-1","capabilities":{"a":1},'
    '"mcp_servers":[],"mcp_server_errors":[]}'
)
RESULT_OK = (
    '{"type":"result","subtype":"success","session_id":"sid-1","total_cost_usd":0.01,'
    '"usage":{"input_tokens":10,"output_tokens":5},'
    '"structured_output":{"signal":"hold","disclaimer":"d"},"transcript_path":"/tmp/t.jsonl"}'
)


class TestNormalizeExitCode:
    def test_signal_deaths_map_to_128_plus_signo(self):
        assert normalize_exit_code(0) == 0
        assert normalize_exit_code(-2) == 130  # SIGINT 收轮
        assert normalize_exit_code(-9) == 137  # SIGKILL 兜底
        assert normalize_exit_code(-15) == 143  # 外部 SIGTERM 弃轮
        assert normalize_exit_code(143) == 143  # 显式 143 原样


class TestParseStream:
    def test_normal_flow(self):
        parsed = _parse_stream([INIT, '{"type":"stream_event","event":"x"}', RESULT_OK])
        assert parsed.result_line_present and parsed.envelope["subtype"] == "success"
        assert parsed.session_id == "sid-1" and parsed.capabilities == {"a": 1}
        assert parsed.structured_output["signal"] == "hold"
        assert parsed.usage_tokens == {"input_tokens": 10, "output_tokens": 5}
        assert parsed.mcp_server_errors == []

    def test_missing_result_line(self):
        parsed = _parse_stream([INIT, '{"type":"stream_event"}'])
        assert not parsed.result_line_present and parsed.envelope is None

    def test_last_result_line_wins(self):
        first = RESULT_OK.replace('"total_cost_usd":0.01', '"total_cost_usd":0.02')
        parsed = _parse_stream([INIT, first, RESULT_OK])
        assert parsed.envelope["total_cost_usd"] == 0.01

    def test_bad_lines_skipped(self):
        parsed = _parse_stream(["{截断", "", "not json", INIT, RESULT_OK])
        assert parsed.result_line_present and parsed.session_id == "sid-1"

    def test_mcp_server_errors_carried(self):
        bad_init = INIT.replace('"mcp_server_errors":[]', '"mcp_server_errors":[{"server":"x"}]')
        parsed = _parse_stream([bad_init, RESULT_OK])
        assert parsed.mcp_server_errors == [{"server": "x"}]

    def test_non_dict_structured_output_treated_missing(self):
        weird = RESULT_OK.replace(
            '"structured_output":{"signal":"hold","disclaimer":"d"}',
            '"structured_output":"oops"',
        )
        parsed = _parse_stream([INIT, weird])
        assert parsed.structured_output is None

    def test_usage_non_int_filtered(self):
        noisy = RESULT_OK.replace(
            '"usage":{"input_tokens":10,"output_tokens":5}',
            '"usage":{"input_tokens":10,"model":"x"}',
        )
        parsed = _parse_stream([INIT, noisy])
        assert parsed.usage_tokens == {"input_tokens": 10}
