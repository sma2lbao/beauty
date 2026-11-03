"""provider 错误:统一成模型可见的错误信封字段,不泄漏上游原文。"""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """一次 provider 调用失败;execute 层把它翻成 {"ok": false, "error": ...}。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable

    def to_error_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


#: 需要用户重新授权的错误码:连接置 expired,前端展示「重新连接」。
REAUTH_ERROR_CODES = frozenset(
    {
        "connector.auth_expired",
        "connector.oauth_failed",
        "connector.scope_missing",
    }
)
