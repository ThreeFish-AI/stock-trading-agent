"""StateLayout：~/.vibe-stock 状态布局与写者归属（方案 §11 读写矩阵的代码化）。

读写矩阵（方案 §11）：

| 路径 | 内容 | 主 Agent | 面板 | launchd 槽 |
| :--- | :--- | :--- | :--- | :--- |
| agent/queue.json | 任务队列+WAL 状态机 | 写 | 读+提交 | — |
| agent/sessions.json | 会话登记 | 写 | 读 | — |
| agent/decisions.jsonl | decision log（hash 链） | 追加写 | 读 | postmortem 消费 |
| agent/audit/<date>/ | stream-json 归档 | 写 | 读 | — |
| agent/budget.json | 预算台账 | 写 | 读写 | — |
| sim-portfolio.json | 模拟账本（根级） | paper 引擎写 | 读 | — |
| monitor/advice/<date>/<code>.md | T2 建议卡（写者例外） | 断言器放行后原子写 | 读 | — |

铁律：既有 SSOT（signals.json / ledger.json 等）对本层全部只读或零接触——本模块不暴露任何
signals/ledger 写 API（vibe-stock 红线 R3/R8 的状态层实现）；monitor/ 其余产物写者仍是
vibe-stock watch.py，本层只消费。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

STATE_ROOT_ENV = "STA_STATE_ROOT"  # 仅测试 / dry-run 注入用；缺省 ~/.vibe-stock


class WriterRole(StrEnum):
    """写者归属枚举（谁有权写该路径）。"""

    AGENT = "agent"  # 主 Agent（编排层）
    PAPER_ENGINE = "paper-engine"  # paper 引擎（本仓）
    WATCHER = "watcher"  # vibe-stock watch.py（外仓，本层零接触）


@dataclass(frozen=True)
class StateLayout:
    """~/.vibe-stock 状态布局（root 可注入，默认读 STA_STATE_ROOT 再回落 ~/.vibe-stock）。"""

    root: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> StateLayout:
        env = dict(os.environ) if env is None else env
        root = Path(env.get(STATE_ROOT_ENV, Path.home() / ".vibe-stock"))
        return cls(root=root)

    # --- agent/ 唯一新写区（写者=主 Agent，方案 §11） ---
    @property
    def agent_dir(self) -> Path:
        return self.root / "agent"

    @property
    def queue_path(self) -> Path:
        return self.agent_dir / "queue.json"

    @property
    def sessions_path(self) -> Path:
        return self.agent_dir / "sessions.json"

    @property
    def decisions_path(self) -> Path:
        return self.agent_dir / "decisions.jsonl"

    @property
    def audit_dir(self) -> Path:
        return self.agent_dir / "audit"

    @property
    def budget_path(self) -> Path:
        return self.agent_dir / "budget.json"

    @property
    def daemon_lock(self) -> Path:
        return self.agent_dir / "daemon.lock"

    # --- 根级与既有 SSOT（只读或零接触） ---
    @property
    def sim_portfolio_path(self) -> Path:
        """模拟账本（07 P3 schema 原样留根；写者=paper 引擎，见 ledger.py）。"""
        return self.root / "sim-portfolio.json"

    @property
    def fetch_lock(self) -> Path:
        """vibe-stock 取数串行锁（尊重：存在即 defer，弱约定；其 flock 实现在外仓）。"""
        return self.root / "fetch_state.lock"

    @property
    def watch_lock(self) -> Path:
        """vibe-stock 盯盘锁（尊重：存在即 defer）。"""
        return self.root / "monitor" / "watch.lock"

    @property
    def advice_dir(self) -> Path:
        """T2 建议卡落位（07 P2 契约路径，agent/ 外唯一写者例外：断言器放行后原子写）。"""
        return self.root / "monitor" / "advice"

    def advice_card_path(self, date: str, code: str) -> Path:
        return self.advice_dir / date / f"{code}.md"
