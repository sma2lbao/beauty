"""MCP server token 应用层加密(M1 本地版)。"""

from __future__ import annotations

import base64
import hashlib

from common.errors import AppError
from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_token(secret: str, value: str) -> str:
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt_token(secret: str, value: str) -> str:
    try:
        return _fernet(secret).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise AppError("mcp.token_unreadable", "MCP token 无法解密") from exc
