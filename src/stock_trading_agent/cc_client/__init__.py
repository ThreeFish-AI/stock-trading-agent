"""CC 大脑层客户端：契约（contract）、CLI 旗标构建（cli）、dry-run 通道（dryrun）。

口径锚：docs/research/main-agent-proposal.md §9（CCClient 契约）与 §13（NR-4 预算闸）。
"""

from stock_trading_agent.cc_client.contract import (
    CCClient,
    CCRequest,
    CCResult,
    ProbeReport,
    derive_session_uuid,
    validate_request,
)

__all__ = [
    "CCClient",
    "CCRequest",
    "CCResult",
    "ProbeReport",
    "derive_session_uuid",
    "validate_request",
]
