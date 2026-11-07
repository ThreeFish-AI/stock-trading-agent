"""结构化输出 schema 族（draft-07；意见段必带，方案 §9 F-11/F-12/F-16）。

全部经 to_draft07() 喂 CC（--json-schema）并在断言器本地复验同一份——喂给 CC 的与本地
复验的必须是同一 schema，禁各处手拼字典。
"""

from __future__ import annotations

from typing import Any

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

ADVICE_SCHEMA: dict[str, Any] = {
    "$schema": _DRAFT07,
    "type": "object",
    "required": ["disclaimer"],
    "properties": {
        "kind": {"type": "string"},
        "advice": {"type": "string"},
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
