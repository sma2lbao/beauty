"""Cookie 会话编解码与 CSRF token。

- 会话 Cookie 用 common.security.SecretBox(Fernet)加密:SPA 读不到,
  HTTP 抓包也看不到 token;
- CSRF 用 double-submit:非 HttpOnly 的随机 cookie + 请求头 X-CSRF-Token;
- Cookie 只放服务端可解的内容,不放可伪造的明文 JSON。
"""

from __future__ import annotations

import secrets
from typing import Any

from common.errors import AppError
from common.security import SecretBox


class SessionCodec(SecretBox):
    """会话 Cookie 编解码:解密失败/非 dict 一律视为未登录(None)。"""

    def encode(self, payload: dict[str, Any]) -> str:
        return self.encrypt_json(payload)

    def decode(self, value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            payload = self.decrypt_json(value)
        except (AppError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
