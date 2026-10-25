"""服务间 token(兼容层):实现已下沉到 common.security.token_broker。

保留本模块是为了不打断既有 import 路径(agent_orchestrator.services.token_broker);
新代码请直接从 common.security.token_broker 引入。
"""

from __future__ import annotations

from common.security.token_broker import (
    DEFAULT_SUBJECT,
    REFRESH_MARGIN_SECONDS,
    TokenBroker,
)

__all__ = ["DEFAULT_SUBJECT", "REFRESH_MARGIN_SECONDS", "TokenBroker"]
