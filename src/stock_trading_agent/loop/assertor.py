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


# 违例类型枚举（§15 断言违例率分桶的桶键；判据只收严——新增检查必须配码）
VIOLATION_CODES = (
    "exit_nonzero",  # 三段判据①：退出码非 0
    "envelope_missing",  # 三段判据②：result 信封缺失
    "result_line_missing",  # 三段判据③：result 行不存在
    "structured_missing",  # subtype=success 但 structured_output 缺失
    "result_subtype_error",  # result subtype 非 success
    "mcp_server_errors",  # init.mcp_server_errors 非空
    "advice_ctx_missing",  # advice kind 未注入 ctx（防漏配）
    "signal_enum_invalid",  # signal 非五词枚举
    "disclaimer_missing",  # 免责段缺失
    "schema_invalid",  # draft-07 本地复验失败
    "sensitive_field",  # 敏感字段词族命中
    "stale_data",  # advice 保鲜门（verdict/opinion_data_date≠今日）
    "anchor_escalation",  # 锚③只降不升违例
    "anchor_unreadable",  # signals.json 不可解析（宁拒不漏）
    "advice_contract",  # advice 契约族（缺字段/走私/非法 outcome）
    "uncategorized",  # 兜底（新检查未配码时聚合于此——维持可观测）
)


@dataclass(frozen=True)
class AssertVerdict:
    ok: bool
    reasons: tuple[str, ...]
    codes: tuple[str, ...] = ()  # 与 reasons 平行（§15 分桶键；0.0.22）

    @classmethod
    def refuse(cls, *reasons: str) -> AssertVerdict:
        return AssertVerdict(
            ok=False, reasons=tuple(reasons), codes=("uncategorized",) * len(reasons)
        )


ADVICE_KINDS = ("monitor_review",)  # T2 建议卡 kind：advice 断言族适用
# 锚③ rank 序：index 越小越激进（strong_buy=0 … strong_sell=4）——「更激进」= rank 更小
_SIGNAL_RANK = {s: i for i, s in enumerate(VALID_SIGNALS)}

_CARD_FIELDS = (
    "candidate_action",
    "final_signal",
    "prev_diff",
    "t1_sell_pct",
    "t1_sell_tier",
    "verdict_date",
    "opinion_data_date",
    "replayed_opinions",
    "trend_ref",
    "rationale",
)


@dataclass(frozen=True)
class AdviceContext:
    """advice 断言族上下文（断言器零 IO——由 loop 层注入）。

    today：市场钟面当日（保鲜门与 verdict_date 比对基准）；
    last_signal：该股最近已登记信号（锚③；None=无记录放行）；
    signals_read_ok：False=signals.json 在场不可解析 → 拒卡（锚不可比即不出卡，宁拒不漏）。
    """

    today: str
    last_signal: str | None
    signals_read_ok: bool


def _advice_family_checks(structured: dict, ctx: AdviceContext) -> list[tuple[str, str]]:
    """T2 建议卡断言族（返回 (code, reason) 对；分支必填程序化——schema 扁平无 if/then）。"""
    pairs: list[tuple[str, str]] = []
    outcome = structured.get("outcome")
    if outcome == "card":
        missing = [f for f in _CARD_FIELDS if f not in structured]
        if missing:
            pairs.append(("advice_contract", f"outcome=card 缺必带字段：{missing}"))
        if not ctx.signals_read_ok:
            pairs.append(
                ("anchor_unreadable", "signals.json 在场不可解析：锚③不可比即不出卡（宁拒不漏）")
            )
        if structured.get("verdict_date") != ctx.today:
            pairs.append(
                (
                    "stale_data",
                    f"verdict_date≠今日（{structured.get('verdict_date')!r}≠{ctx.today!r}）",
                )
            )
        if structured.get("opinion_data_date") != ctx.today:
            stale = structured.get("opinion_data_date")
            pairs.append(
                (
                    "stale_data",
                    f"opinion_data_date≠今日（意见保鲜机械门：数据日 "
                    f"{stale!r}≠{ctx.today!r}，须重产）",
                )
            )
        final_signal = structured.get("final_signal")
        if (
            final_signal in _SIGNAL_RANK
            and ctx.last_signal in _SIGNAL_RANK
            and _SIGNAL_RANK[final_signal] < _SIGNAL_RANK[ctx.last_signal]
        ):
            pairs.append(
                (
                    "anchor_escalation",
                    f"只降不升锚③违例：盘中终版 {final_signal} 比最近登记 "
                    f"{ctx.last_signal} 更激进（拒出卡降级纯事实通报）",
                )
            )
    elif outcome == "facts_only":
        if "facts_note" not in structured:
            pairs.append(("advice_contract", "outcome=facts_only 缺 facts_note"))
        smuggled = [f for f in _CARD_FIELDS if f in structured]
        if smuggled:
            pairs.append(
                (
                    "advice_contract",
                    f"outcome=facts_only 出现卡面字段（降级通道走私卡面）：{smuggled}",
                )
            )
    else:
        pairs.append(("advice_contract", f"outcome 非法：{outcome!r}（card | facts_only）"))
    return pairs


def assess_result(
    result: CCResult,
    kind: str,
    *,
    schema: dict[str, Any] | None = None,
    advice_ctx: AdviceContext | None = None,
) -> AssertVerdict:
    """三段判据 + 结构化输出在场 + 红线族 + draft-07 本地复验；违例即 refuse（Loop 据此拒落盘）。

    advice kind 必须注入 advice_ctx（缺省即拒——防漏配，判据只收严）。
    """
    pairs: list[tuple[str, str]] = []
    if result.exit_code != 0:
        pairs.append(("exit_nonzero", f"退出码非 0：{result.exit_code}"))
    if result.envelope is None:
        pairs.append(
            ("envelope_missing", "result 信封缺失（stream 截断或静默丢 result，D-05/D-06）")
        )
    if not result.result_line_present:
        pairs.append(("result_line_missing", "result 行不存在"))
    if (
        result.envelope is not None
        and result.envelope.get("subtype") == "success"
        and result.structured_output is None
    ):
        pairs.append(
            (
                "structured_missing",
                "subtype=success 但 structured_output 缺失（strict 静默回退不可自证，F-14/F-15）",
            )
        )
    if result.envelope is not None and result.envelope.get("subtype") != "success":
        # 收严（0.0.4）：运行内失败打印为 result（D-01，退出码不可单独判定）
        pairs.append(
            (
                "result_subtype_error",
                f"result subtype 非 success：{result.envelope.get('subtype')!r}",
            )
        )
    if result.mcp_server_errors:
        # 收严（0.0.4）：坏 mcp 配置被静默跳过，以 init.mcp_server_errors 非空为门禁（F-23）
        pairs.append(
            (
                "mcp_server_errors",
                "init.mcp_server_errors 非空（F-23）："
                + str([e.get("server") for e in result.mcp_server_errors]),
            )
        )

    structured = result.structured_output
    if kind in ADVICE_KINDS and advice_ctx is None:
        pairs.append(("advice_ctx_missing", "advice kind 未注入 advice_ctx（防漏配，收严）"))
    if structured is not None:
        if kind in OPINION_KINDS and structured.get("signal") not in VALID_SIGNALS:
            pairs.append(
                ("signal_enum_invalid", f"signal 非五词枚举：{structured.get('signal')!r}")
            )
        if not str(structured.get("disclaimer", "")).strip():
            pairs.append(
                (
                    "disclaimer_missing",
                    "免责段缺失（全部产物必带：AI 生成标注+复核前置，方案 §7 免责四件套）",
                )
            )
        if schema is not None:
            pairs.extend(("schema_invalid", r) for r in _validate_draft07(structured, schema))
        pairs.extend(("sensitive_field", r) for r in scan_sensitive_fields(structured))
        if kind in ADVICE_KINDS and advice_ctx is not None:
            pairs.extend(_advice_family_checks(structured, advice_ctx))
    reasons = tuple(r for _, r in pairs)
    codes = tuple(c for c, _ in pairs)
    return AssertVerdict(ok=not reasons, reasons=reasons, codes=codes)


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
