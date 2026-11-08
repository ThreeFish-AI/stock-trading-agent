"""agent/config.json：L2 无人值守总闸（默认关；显式开启方生效，NR-4「无闸即拒起」的闸位）。

R1 联动语义（12-orchestration §⑩）：l2_enabled=true → 预算闸 enabled 联动置 True，
billing_mode 由 probe 判定后持久化于此（不探测的消费者读存值，None=未探测回落 "api" 收紧）；
l2_enabled=false → 预算闸回落默认关（人在环仅记账）。加载纪律：缺席/坏 JSON/版本不符一律
回落默认关（宁拒不放——配置不可信时不得放行无人值守）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from stock_trading_agent.state.store import atomic_write_json

CONFIG_VERSION = 1


@dataclass
class AgentConfig:
    version: int = CONFIG_VERSION
    l2_enabled: bool = False  # 默认关（M3 无人值守总闸，方案 §3）
    billing_mode: str | None = None  # probe 判定持久化；None=未探测（消费侧按 api 收紧）
    updated_at: str = ""
    extra: dict = field(default_factory=dict)  # 前向扩展位（未知键保真，不丢用户配置）

    def to_dict(self) -> dict:
        out = {
            "version": self.version,
            "l2_enabled": self.l2_enabled,
            "billing_mode": self.billing_mode,
            "updated_at": self.updated_at,
        }
        out.update(self.extra)
        return out


def load_agent_config(path) -> AgentConfig:
    """读 config；缺席/坏 JSON/版本不符 → 默认关（宁拒不放）。"""
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return AgentConfig()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AgentConfig()
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        return AgentConfig()
    known = {"version", "l2_enabled", "billing_mode", "updated_at"}
    return AgentConfig(
        l2_enabled=bool(data.get("l2_enabled", False)),
        billing_mode=data.get("billing_mode"),
        updated_at=str(data.get("updated_at", "")),
        extra={k: v for k, v in data.items() if k not in known},
    )


def save_agent_config(path, cfg: AgentConfig) -> None:
    """原子写（os.replace；写者=主 Agent——面板开关端点进程内直调）。"""
    cfg.updated_at = _dt.datetime.now(tz=_dt.UTC).isoformat(timespec="seconds")
    atomic_write_json(path, cfg.to_dict())
