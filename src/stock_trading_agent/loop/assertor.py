"""④ 确定性后处理断言器（NR-3 落盘前置门，方案 §10④；判据族只收严）。

三段完成判据：退出码 0 ∧ result 信封在场 ∧ result 行存在（exit 0 不代表业务成功，D-01/D-05/D-06）；
subtype=success 而 structured_output 缺失 → 失败（strict 静默回退不可自证，F-14/F-15）。
红线断言族：signal 五词枚举（意见类）、免责段在场（**全部** 落盘产物 kind，§7 免责四件套）、
draft-07 本地复验（schema 在场时）、敏感字段词族扫描（R2/NR-5，子串匹配防同义变体绕过）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jsonschema

from stock_trading_agent.cc_client.contract import CCResult

VALID_SIGNALS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
OPINION_KINDS = ("diagnose_full", "diagnose_quick", "batch_wave")  # 意见类 kind：signal 必带

# 敏感字段词族（R2/NR-5：成本/数量/持仓/可反推派生量零落盘）——子串匹配（大小写不敏感），
# 防同义/变体键绕过（quantity/shares/lots/pnl/avgCost 等）；宁误拒不漏放（只收严向）。
SENSITIVE_KEY_PATTERN = re.compile(
    r"cost|qty|quantit|share|lots|volume|amount|pnl|market_value|basis|invested|weight"
    r"|sellable|position_size|holding|account|broker|cookie|api_?key|secret|token|credential",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AssertVerdict:
    ok: bool
    reasons: tuple[str, ...]

    @classmethod
    def refuse(cls, *reasons: str) -> AssertVerdict:
        return AssertVerdict(ok=False, reasons=tuple(reasons))


def assess_result(
    result: CCResult, kind: str, *, schema: dict[str, Any] | None = None
) -> AssertVerdict:
    """三段判据 + 结构化输出在场 + 红线族 + draft-07 本地复验；违例即 refuse（Loop 据此拒落盘）。"""
    reasons: list[str] = []
    if result.exit_code != 0:
        reasons.append(f"退出码非 0：{result.exit_code}")
    if result.envelope is None:
        reasons.append("result 信封缺失（stream 截断或静默丢 result，D-05/D-06）")
    if not result.result_line_present:
        reasons.append("result 行不存在")
    if (
        result.envelope is not None
        and result.envelope.get("subtype") == "success"
        and result.structured_output is None
    ):
        reasons.append(
            "subtype=success 但 structured_output 缺失（strict 静默回退不可自证，F-14/F-15）"
        )

    structured = result.structured_output
    if structured is not None:
        if kind in OPINION_KINDS and structured.get("signal") not in VALID_SIGNALS:
            reasons.append(f"signal 非五词枚举：{structured.get('signal')!r}")
        if not str(structured.get("disclaimer", "")).strip():
            reasons.append("免责段缺失（全部产物必带：AI 生成标注+复核前置，方案 §7 免责四件套）")
        if schema is not None:
            reasons.extend(_validate_draft07(structured, schema))
        reasons.extend(scan_sensitive_fields(structured))
    return AssertVerdict(ok=not reasons, reasons=tuple(reasons))


def _validate_draft07(payload: dict, schema: dict[str, Any]) -> list[str]:
    """draft-07 本地复验（与喂给 CC 的 --json-schema 同一份；NR-3）。"""
    checker = jsonschema.Draft7Validator(schema)
    errors = sorted(checker.iter_errors(payload), key=lambda e: list(e.absolute_path))
    return [
        "schema 复验失败：" + "/".join(str(p) for p in err.absolute_path) + " " + err.message
        for err in errors
    ]


def scan_sensitive_fields(payload: dict) -> list[str]:
    """敏感字段词族扫描（R2/NR-5：成本/数量/持仓/派生量零落盘；嵌套递归；子串匹配）。"""
    hits: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_s = str(key)
                if SENSITIVE_KEY_PATTERN.search(key_s) and value not in (None, ""):
                    hits.append(f"敏感字段在场：{path}{key}")
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}].")

    walk(payload, "")
    return hits
