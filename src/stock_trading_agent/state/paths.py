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
        """唯一消费者锁：server worker 线程或 CLI run-once 二选一（12-orchestration §⑤）。"""
        return self.agent_dir / "daemon.lock"

    @property
    def queue_lock(self) -> Path:
        """queue.json 读-改-写串行闸：任何变更周期 = 本锁内「重载→变更→落盘」。"""
        return self.agent_dir / "queue.lock"

    @property
    def decisions_lock(self) -> Path:
        """decisions.jsonl 追加串行闸（尾部对账+追加整个周期；最内层锁，12-orchestration §④）。"""
        return self.agent_dir / "decisions.lock"

    @property
    def runs_dir(self) -> Path:
        """会话/任务工作目录父级（agent/runs/；CC 会话 cwd 与 T1 workdir 均落此下）。"""
        return self.agent_dir / "runs"

    def t1_workdir(self, date: str, code: str) -> Path:
        """T1 刷新工作目录：agent/runs/<date>/t1/<code>/（evidence+context-pack 落位）。"""
        return self.runs_dir / date / "t1" / code

    @property
    def briefing_dir(self) -> Path:
        """晨报/复盘行动项卡（agent/briefing/<date>/；早晚分卡 0.0.18）。"""
        return self.agent_dir / "briefing"

    def briefing_card_path(self, date: str, code: str, slot: str = "") -> Path:
        """卡路径：slot am|pm → <code>.am.md / <code>.pm.md；""=legacy 单卡 <code>.md。"""
        suffix = f".{slot}" if slot in ("am", "pm") else ""
        return self.briefing_dir / date / f"{code}{suffix}.md"

    @property
    def agent_config_path(self) -> Path:
        """L2 总闸配置（agent/config.json；写者=主 Agent，12-orchestration §⑩）。"""
        return self.agent_dir / "config.json"

    @property
    def watcher_state_path(self) -> Path:
        """monitor 台账监听偏移断点（agent/watcher.json；崩溃不重放已登记事件）。"""
        return self.agent_dir / "watcher.json"

    # --- vibe-stock 既有产物（只读消费；写者=外仓脚本，R3/R8） ---
    @property
    def monitor_dir(self) -> Path:
        return self.root / "monitor"

    @property
    def monitor_events_dir(self) -> Path:
        return self.monitor_dir / "events"

    def monitor_events_file(self, date: str) -> Path:
        return self.monitor_events_dir / f"{date}.jsonl"

    @property
    def monitor_state_path(self) -> Path:
        """watch.py 运行时状态（close_done/next_due 等三槽状态条数据源，只读）。"""
        return self.monitor_dir / "state.json"

    @property
    def outlook_dir(self) -> Path:
        return self.root / "outlook"

    def outlook_file(self, date: str) -> Path:
        return self.outlook_dir / f"{date}.md"

    @property
    def universe_path(self) -> Path:
        """monitor/universe.json（盯盘宇宙，只读消费：T2 实盘优先判据的数据源）。"""
        return self.monitor_dir / "universe.json"

    @property
    def reports_dir(self) -> Path:
        """vibe-stock 全诊报告目录（<code>.<market>/<date>.md，只读）。"""
        return self.root / "reports"

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
    def sim_pending_fills_path(self) -> Path:
        """确认队列（根级；写者=paper 引擎，13-paper §⑤；瞬态非 append-only）。"""
        return self.root / "sim-pending-fills.json"

    @property
    def sim_lock(self) -> Path:
        """paper 读-改-写串行闸（pending+portfolio 双文件周期，防面板线程池并发双写）。"""
        return self.root / "sim.lock"

    @property
    def advice_dir(self) -> Path:
        """T2 建议卡落位（07 P2 契约路径，agent/ 外唯一写者例外：断言器放行后原子写）。"""
        return self.root / "monitor" / "advice"

    def advice_card_path(self, date: str, code: str) -> Path:
        return self.advice_dir / date / f"{code}.md"
