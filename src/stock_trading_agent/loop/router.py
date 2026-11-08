"""② 意图路由：YAML 确定性查表 + 兜底计数（零 LLM，方案 §10②；命中率指标见 §15）。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

DEFAULT_TABLE = "routing.yaml"


@dataclass(frozen=True)
class TaskTemplate:
    kind: str
    task_kind: str
    prompt_template: str
    schema_ref: str
    budget_usd: float
    max_turns: int
    allowed_tools: tuple[str, ...]


@dataclass
class RoutingTable:
    routes: dict[str, TaskTemplate]
    zero_llm: dict[str, str]
    defaults: dict
    fallback_count: int = 0  # 未知 kind 兜底桶计数（§15 意图路由命中率指标的代码位）

    def route(self, kind: str) -> TaskTemplate | None:
        template = self.routes.get(kind)
        if template is None:
            self.fallback_count += 1
        return template


class RoutingError(ValueError):
    """路由表非法（缺 routes / 模板缺 NR-4 必填字段）。"""


def parse_table(data: dict) -> RoutingTable:
    """解析并校验路由表：模板必须带 budget_usd>0 与 max_turns>0（NR-4 在路由层第二道闸）。"""
    routes: dict[str, TaskTemplate] = {}
    for entry in data.get("routes", []):
        template = TaskTemplate(
            kind=entry["kind"],
            task_kind=entry.get("task_kind", entry["kind"]),
            prompt_template=entry["prompt_template"],
            schema_ref=entry.get("schema_ref", ""),
            budget_usd=float(entry["budget_usd"]),
            max_turns=int(entry["max_turns"]),
            allowed_tools=tuple(entry.get("allowed_tools", ())),
        )
        if template.budget_usd <= 0 or template.max_turns <= 0:
            raise RoutingError(f"路由 {template.kind} 违反 NR-4：budget_usd/max_turns 必须 > 0")
        routes[template.kind] = template
    return RoutingTable(
        routes=routes,
        zero_llm=dict(data.get("zero_llm", {})),
        defaults=dict(data.get("defaults", {})),
    )


def load_default_table() -> RoutingTable:
    raw = (
        resources.files("stock_trading_agent.loop")
        .joinpath(DEFAULT_TABLE)
        .read_text(encoding="utf-8")
    )
    return parse_table(yaml.safe_load(raw))


def load_table(path: Path) -> RoutingTable:
    """用户路径覆盖（包内默认表的显式替换；**判据只许收严**，放宽即拒）。

    收严判据：对默认表已存在的 kind，覆盖表的 budget_usd / max_turns 只许 ≤ 默认值、
    allowed_tools 只许为默认值子集——放宽（预算↑/轮次↑/工具扩）抛 RoutingError。
    新增 kind 允许（仍须过 NR-4 解析闸）；删除 kind 允许（收窄面）。
    """
    table = parse_table(yaml.safe_load(path.read_text(encoding="utf-8")))
    baseline = load_default_table()
    for kind, default in baseline.routes.items():
        override = table.routes.get(kind)
        if override is None:
            continue
        if override.budget_usd > default.budget_usd:
            raise RoutingError(
                f"路由 {kind} 放宽被拒：budget_usd {override.budget_usd}>"
                + f"默认 {default.budget_usd}（只许收严）"
            )
        if override.max_turns > default.max_turns:
            raise RoutingError(
                f"路由 {kind} 放宽被拒：max_turns {override.max_turns}>"
                + f"默认 {default.max_turns}（只许收严）"
            )
        extra_tools = set(override.allowed_tools) - set(default.allowed_tools)
        if extra_tools:
            raise RoutingError(
                f"路由 {kind} 放宽被拒：allowed_tools 扩集 {sorted(extra_tools)}（只许收严）"
            )
    return table
