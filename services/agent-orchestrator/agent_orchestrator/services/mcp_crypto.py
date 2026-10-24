"""MCP server token 应用层加密:实现委托 common.security,错误码契约不变。"""

from __future__ import annotations

from common.security import SecretBox

_ERROR_CODE = "mcp.token_unreadable"
_ERROR_MESSAGE = "MCP token 无法解密"


def _box(secret: str) -> SecretBox:
    return SecretBox(secret, error_code=_ERROR_CODE, error_message=_ERROR_MESSAGE)


def encrypt_token(secret: str, value: str) -> str:
    return _box(secret).encrypt(value)


def decrypt_token(secret: str, value: str) -> str:
    return _box(secret).decrypt(value)
