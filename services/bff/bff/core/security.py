"""Cookie 会话加解密与 CSRF token。

- 会话 Cookie 用 Fernet(AES128-CBC + HMAC)加密:SPA 读不到,HTTP 抓包也看不到 token;
- CSRF 用 double-submit:非 HttpOnly 的随机 cookie + 请求头 X-CSRF-Token;
- Cookie 只放服务端可解的内容,不放可伪造的明文 JSON。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SessionCodec:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(secret.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encode(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._fernet.encrypt(raw.encode()).decode()

    def decode(self, value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            raw = self._fernet.decrypt(value.encode())
            payload = json.loads(raw.decode())
        except (InvalidToken, ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
