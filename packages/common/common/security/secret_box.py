"""Fernet 对称加密封装(security 原语)。

密钥派生规则固定为 sha256(secret) 后做 urlsafe base64,与历史各服务
本地实现逐字节一致——旧密文必须能被新实现解密,反之亦然(回滚安全)。
错误码/文案由使用方注入,保持各服务既有对外错误契约不变。
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from common.errors import AppError


def derive_key(secret: str) -> bytes:
    """把任意长度密钥派生为 Fernet 要求的 32 字节 url-safe base64 key。"""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


class SecretBox:
    """对称加解密盒:加密文本/JSON,解密失败抛统一 AppError。"""

    def __init__(
        self,
        secret: str,
        *,
        error_code: str = "secret_box.unreadable",
        error_message: str = "密文无法解密",
    ) -> None:
        self._fernet = Fernet(derive_key(secret))
        self._error_code = error_code
        self._error_message = error_message

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise AppError(self._error_code, self._error_message) from exc

    def encrypt_json(self, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self.encrypt(raw)

    def decrypt_json(self, value: str) -> Any:
        return json.loads(self.decrypt(value))
