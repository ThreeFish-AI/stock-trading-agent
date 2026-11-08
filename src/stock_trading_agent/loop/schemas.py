"""结构化输出 schema 族（draft-07；意见段必带，方案 §9 F-11/F-12/F-16）。

全部经 to_draft07() 喂 CC（--json-schema）并在断言器本地复验同一份——喂给 CC 的与本地
复验的必须是同一 schema，禁各处手拼字典。
"""

from __future__ import annotations

from typing import Any

from stock_trading_agent.loop.advice import ADVICE_ACTIONS

_DRAFT07 = "http://json-schema.org/draft-07/schema#"

_SIGNAL_ENUM = ["strong_buy", "buy", "hold", "sell", "strong_sell"]

OPINION_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT07,
    "type": "object",
    "required": ["signal", "disclaimer"],
    "properties": {
        "kind": {"type": "string"},
        "signal": {"enum": _SIGNAL_ENUM},
        "rationale": {"type": "string"},
        "disclaimer": {"type": "string", "minLength": 1},
    },
}

# T2 建议卡结构化契约（monitor-advice/1 的 structured 面；07 P2 + 04 五档 + NR-1）。
# 扁平子集（无 if/then）：outcome=card/facts_only 的分支必填由断言器程序化判定——
# 防 CC 端 schema 方言风险（F-20）。字段名避开断言器敏感词族（禁 sellable 前缀等）。
ADVICE_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT07,
    "type": "object",
    "required": ["outcome", "disclaimer"],
    "properties": {
        "kind": {"type": "string"},
        "outcome": {"enum": ["card", "facts_only"]},
        # --- outcome=card 必带（断言器程序化） ---
        "candidate_action": {"enum": list(ADVICE_ACTIONS)},
        "final_signal": {"enum": _SIGNAL_ENUM},
        "prev_diff": {
            "type": "object",
            "properties": {
                "prev_final_signal": {"enum": [*_SIGNAL_ENUM, "none"]},
                "note": {"type": "string", "minLength": 1, "maxLength": 200},
            },
        },
        "t1_sell_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "t1_sell_tier": {"type": "string", "minLength": 1, "maxLength": 20},
        "verdict_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "opinion_data_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "replayed_opinions": {"type": "integer", "minimum": 2, "maximum": 50},
        "trend_ref": {
            "type": "object",
            "properties": {
                "band_rows": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "horizon": {"enum": ["1d", "3d", "5d", "10d"]},
                            "low": {"type": "number", "exclusiveMinimum": 0},
                            "high": {"type": "number", "exclusiveMinimum": 0},
                            "prob_touch_low_pct": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 100,
                            },
                        },
                    },
                }
            },
        },
        "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
        # --- outcome=facts_only 必带（断言器程序化；卡面字段必须全缺席防走私） ---
        "facts_note": {"type": "string", "minLength": 1, "maxLength": 1000},
        "disclaimer": {"type": "string", "minLength": 1},
    },
}

FILL_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT07,
    "type": "object",
    "required": ["disclaimer"],
    "properties": {
        "kind": {"type": "string"},
        "fill": {"type": "object"},
        "disclaimer": {"type": "string", "minLength": 1},
    },
}

BRIEFING_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT07,
    "type": "object",
    "required": ["disclaimer"],
    "properties": {
        "kind": {"type": "string"},
        "briefing": {"type": "string"},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "disclaimer": {"type": "string", "minLength": 1},
    },
}

# routing.yaml 的 schema_ref → schema（每 kind 落盘产物必带免责段，§7 免责四件套）
SCHEMA_BY_REF: dict[str, dict[str, Any]] = {
    "opinion": OPINION_SCHEMA,
    "advice": ADVICE_SCHEMA,
    "fill": FILL_SCHEMA,
    "briefing": BRIEFING_SCHEMA,
}


def schema_for_ref(schema_ref: str) -> dict[str, Any] | None:
    return SCHEMA_BY_REF.get(schema_ref)
