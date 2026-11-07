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
