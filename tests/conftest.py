"""共享 fixture：tmp 状态布局（绝不触真实 ~/.vibe-stock）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_trading_agent.state.paths import StateLayout


@pytest.fixture
def tmp_layout(tmp_path: Path) -> StateLayout:
    return StateLayout(root=tmp_path / "state")


@pytest.fixture
def dry_audit(tmp_layout: StateLayout) -> Path:
    tmp_layout.audit_dir.mkdir(parents=True, exist_ok=True)
    return tmp_layout.audit_dir


@pytest.fixture
def make_runtime(tmp_path: Path):
    """LoopRuntime 工厂：unit/integration 共用；缺省 dry-run 通道 + watcher 关（无线程）。"""

    from stock_trading_agent.cc_client.dryrun import DryRunCCClient
    from stock_trading_agent.loop.t1 import T1Runner
    from stock_trading_agent.loop.worker import LoopRuntime
    from stock_trading_agent.state.paths import StateLayout

    def _make(*, cc=None, t1=None, root: Path | None = None) -> LoopRuntime:
        layout = StateLayout(root=root or tmp_path / "state")
        return LoopRuntime(
            layout=layout,
            cc=cc if cc is not None else DryRunCCClient(audit_dir=layout.audit_dir),
            t1=t1 if t1 is not None else T1Runner(),
            watcher_enabled=False,
        )

    return _make
