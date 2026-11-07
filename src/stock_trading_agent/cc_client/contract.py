"""CCRequest / CCResult / ProbeReport 契约与 CCClient Protocol（方案 §9 签名级）。

NR-4 类型层闸：budget_usd / max_turns 每任务必填且 > 0，validate_request 拒绝缺省与非正值。
坑位（F-12）：CC `--json-schema` 按 draft-07 校验，pydantic model_json_schema() 默认吐 2020-12——
任何喂给 CC 的 schema 一律经 to_draft07() 单一转换函数收口，禁止各处手拼 schema 字典。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_SESSION_NS = uuid.UUID("ba7eba11-0000-4000-8000-5700c0de0000")


@dataclass(frozen=True)
class CCRequest:
    """一次 CC 会话请求（方案 §9；字段与旗标一一对应）。"""

    task_id: str
    session_uuid: str  # UUIDv5(ns, f"{date}:{code}:{kind}")（F-60）
    prompt: str | Path  # Skill 入口「/vibe-stock 600519」形态（F-49）
    schema: dict[str, Any] | None  # draft-07 扁平子集，意见段必带（F-11/F-12/F-16）
    budget_usd: float  # NR-4：每任务必填 > 0
    max_turns: int  # NR-4：每任务必填 > 0
    allowed_tools: tuple[str, ...]
    permission_mode: str = "dontAsk"  # F-24
    mcp: str = "strict-empty"  # --strict-mcp-config + 空 --mcp-config（D-53：429k→2.8k tokens）
    timeout_s: int = 900
    autocompact: str | None = None


@dataclass
class CCResult:
    """一次 CC 会话结果（三段判据的输入：退出码 + result 信封 + result 行存在）。"""

    exit_code: int
    envelope: dict[str, Any] | None  # subtype / total_cost_usd / usage / session_id
    structured_output: dict[str, Any] | None
    result_line_present: bool
    transcript_path: Path
    usage_tokens: dict[str, int] = field(default_factory=dict)


@dataclass
class ProbeReport:
    """CC 通道探针（--version + 认证态 + init.capabilities；F-10/F-31）。"""

    cli_version: str
    auth_mode: str  # subscription | api | dry-run
    capabilities: dict[str, Any] = field(default_factory=dict)


class CCClient(Protocol):
    """CC 大脑层通道协议（headless CLI 子进程为大脑主通道；Agent SDK 是 M2 对照通道 D6）。"""

    def spawn_once(self, req: CCRequest) -> CCResult: ...

    def resume(self, session_id: str, req: CCRequest) -> CCResult:
        # 续话一律 --resume 禁 --continue，旗标全量重传（D-32/D-37/D-76）
        ...

    def probe(self) -> ProbeReport: ...


class ContractError(ValueError):
    """契约校验失败（NR-4 类型层闸拒绝）。"""


def derive_session_uuid(date: str, code: str, kind: str) -> str:
    """UUIDv5(ns, f"{date}:{code}:{kind}")——会话身份确定性派生（F-60）。"""
    return str(uuid.uuid5(_SESSION_NS, f"{date}:{code}:{kind}"))


def to_draft07(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """任何喂给 --json-schema 的 schema 的单一转换口（F-12 收口）。

    做法：剥离/降写 $schema 声明为 draft-07；None 原样透传。
    坑位：pydantic v2 model_json_schema() 默认 2020-12 方言，CC 端按 draft-07 校验会启动即拒。
    """
    if schema is None:
        return None
    out = dict(schema)
    dollar = out.get("$schema")
    if isinstance(dollar, str) and "2020-12" in dollar:
        raise ContractError(
            "schema 声明为 2020-12 方言：禁止直喂 CC（--json-schema 按 draft-07 校验），"
            "请经 to_draft07() 剥离/降写后再传（F-12）"
        )
    out.pop("$schema", None)
    return out


def validate_request(req: CCRequest) -> None:
    """NR-4 类型层闸：budget_usd / max_turns 必填且 > 0；task_id / prompt 非空。

    坑位：subscription 计费形态下 --max-budget-usd 仍须带高位哨兵值满足 NR-4 旗标在场性
    （数值放开属预算闸层，见 budget.gate；此处只管字段在场与合法）。
    """
    if not req.task_id:
        raise ContractError("task_id 不能为空")
    if isinstance(req.prompt, str) and not req.prompt.strip():
        raise ContractError("prompt 不能为空")
    if req.budget_usd is None or req.budget_usd <= 0:
        raise ContractError("NR-4：budget_usd 必填且 > 0")
    if req.max_turns is None or req.max_turns <= 0:
        raise ContractError("NR-4：max_turns 必填且 > 0")
    if not req.allowed_tools:
        raise ContractError("allowed_tools 不能为空（精确白名单，F-24）")
