"""安全原语:对称加密、进程内限流、服务间内部调用鉴权。

各服务不得再自行实现 Fernet 派生/加解密或滑动窗口限流,统一引用本包,
保证密钥派生规则与历史密文逐字节兼容(R-25)。
"""

from __future__ import annotations

from common.security.internal_secret import (
    make_internal_secret_dependency,
    require_internal_secret,
)
from common.security.rate_limit import SlidingWindowLimiter
from common.security.secret_box import SecretBox, derive_key

__all__ = [
    "SecretBox",
    "SlidingWindowLimiter",
    "derive_key",
    "make_internal_secret_dependency",
    "require_internal_secret",
]
