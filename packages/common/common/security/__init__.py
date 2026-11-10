"""安全原语:对称加密、限流(内存/Redis)、服务间内部调用鉴权。

各服务不得再自行实现 Fernet 派生/加解密或滑动窗口限流,统一引用本包,
保证密钥派生规则与历史密文逐字节兼容(R-25)。多副本限流用
build_rate_limiter + REDIS_URL 装配(见 docs/design/rate-limiting.md)。
"""

from __future__ import annotations

from common.security.internal_secret import (
    ACCOUNT_HEADER,
    SECRET_HEADER,
    make_internal_secret_dependency,
    require_internal_secret,
)
from common.security.rate_limit import (
    RateLimitStore,
    RedisFixedWindowLimiter,
    RedisRateLimitStore,
    SlidingWindowLimiter,
    build_rate_limiter,
)
from common.security.secret_box import SecretBox, derive_key
from common.security.token_broker import TokenBroker

__all__ = [
    "SECRET_HEADER",
    "ACCOUNT_HEADER",
    "RateLimitStore",
    "RedisFixedWindowLimiter",
    "RedisRateLimitStore",
    "SecretBox",
    "SlidingWindowLimiter",
    "TokenBroker",
    "build_rate_limiter",
    "derive_key",
    "make_internal_secret_dependency",
    "require_internal_secret",
]
