"""模拟盘引擎（纯确定性模块，零 LLM 参与；NR-2：永不接真实下单接口）。

口径 SSOT 在 vibe-stock：三批建仓与跳空回落见
https://github.com/ThreeFish-AI/vibe-stock/blob/main/references/06-batch-scan.md（Stage D）；
批状态机、仓位推导与成交/提示两把尺见
https://github.com/ThreeFish-AI/vibe-stock/blob/main/references/07-monitor.md（sim 组合 SSOT 节）。
本包注释只放指针，不复制口径文字；语义以代码与测试为准。
"""

from stock_trading_agent.paper.fills import Fill, match_limit_buy, match_limit_sell, match_stop
from stock_trading_agent.paper.model import Operation, Position, SimPortfolio, Tranche

__all__ = [
    "Fill",
    "Operation",
    "Position",
    "SimPortfolio",
    "Tranche",
    "match_limit_buy",
    "match_limit_sell",
    "match_stop",
]
