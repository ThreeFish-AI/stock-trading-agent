"""DryRunCCClient：合成 CC 会话通道（Loop 六段与 CI 的唯一默认通道）。

产出三段判据可过的合成信封（exit 0 + subtype=success + result 行在场 + 最小合法
structured_output），并把合成 stream-json 行真实写入 agent/audit/<date>/——审计归档
路径在骨架期即被跑通（方案 §11）。
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from stock_trading_agent.cc_client.contract import CCRequest, CCResult, ProbeReport

_OPINION_SIGNALS = ("strong_buy", "buy", "hold", "sell", "strong_sell")


def _minimal_structured_output(kind: str) -> dict:
    """按 kind 给最小合法对象（意见类 kind 带 signal 五词枚举内合法值与免责段，供断言器过门）。"""
    from stock_trading_agent.loop.assertor import OPINION_KINDS

    base: dict = {"kind": kind, "disclaimer": "不构成投资建议；AI 生成，用户复核前置"}
    if kind in OPINION_KINDS:
        base["signal"] = "hold"
        base["rationale"] = "dry-run 合成意见（非真实裁决）"
    return base


class DryRunCCClient:
    """合成通道：接口与 HeadlessCLIClient 同构，永不触网络与本机 claude CLI。"""

    def __init__(self, audit_dir: Path | None = None) -> None:
        # audit_dir 缺省 None：不落归档（纯单测用）；注入时真实写 agent/audit/<date>/
        self._audit_dir = audit_dir
        self.calls: list[CCRequest] = []

    def _archive(self, req: CCRequest, lines: list[dict]) -> None:
        if self._audit_dir is None:
            return
        day = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")
        out_dir = self._audit_dir / day
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"dryrun-{req.task_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _result(self, req: CCRequest, session_id: str) -> CCResult:
        structured = _minimal_structured_output(kind=req.task_id.split(":", 1)[0])
        transcript_lines = [
            {"type": "system", "subtype": "init", "session_id": session_id},
            {"type": "stream_event", "event": "dry-run"},
            {
                "type": "result",
                "subtype": "success",
                "session_id": session_id,
                "total_cost_usd": 0.0,
            },
        ]
        self._archive(req, transcript_lines)
        audit_day = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d")
        transcript_path = (
            self._audit_dir / audit_day / f"dryrun-{req.task_id}.jsonl"
            if self._audit_dir is not None
            else Path("/dev/null")
        )
        return CCResult(
            exit_code=0,
            envelope={"subtype": "success", "session_id": session_id, "total_cost_usd": 0.0},
            structured_output=structured,
            result_line_present=True,
            transcript_path=transcript_path,
            usage_tokens={"input_tokens": 0, "output_tokens": 0},
        )

    def spawn_once(self, req: CCRequest) -> CCResult:
        self.calls.append(req)
        return self._result(req, session_id=req.session_uuid)

    def resume(self, session_id: str, req: CCRequest) -> CCResult:
        self.calls.append(req)
        return self._result(req, session_id=session_id)

    def probe(self) -> ProbeReport:
        return ProbeReport(cli_version="dry-run", auth_mode="dry-run", capabilities={})
