"""BYOK 应用层加密(M0-11 占位):只加密存储,暂不参与调用。"""

from __future__ import annotations

import base64
import hashlib

from common.errors import AppError
from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(secret: str, value: str) -> str:
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt_key(secret: str, value: str) -> str:
    try:
        return _fernet(secret).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise AppError("byok.decrypt_failed", "BYOK 密钥无法解密") from exc


def mask_key(value: str) -> str:
    return "••••" + value[-4:] if len(value) >= 4 else "••••"
