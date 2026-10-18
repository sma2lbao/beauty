"""连接器凭证应用层加密(M0 本地版):读端永不返回,传输时解密。"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from common.errors import AppError
from cryptography.fernet import Fernet, InvalidToken

_ENC_PREFIX = "fernet:"


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credentials(secret: str, credentials: dict[str, Any]) -> dict[str, str]:
    raw = json.dumps(credentials, ensure_ascii=False, separators=(",", ":"))
    return {
        "v": "1",
        "ciphertext": _ENC_PREFIX + _fernet(secret).encrypt(raw.encode()).decode(),
    }


def decrypt_credentials(secret: str, stored: dict[str, Any]) -> dict[str, Any]:
    """兼容历史明文行:没有 ciphertext 字段时按明文返回。"""
    ciphertext = stored.get("ciphertext") if isinstance(stored, dict) else None
    if not ciphertext:
        return dict(stored or {})
    token = str(ciphertext)
    if token.startswith(_ENC_PREFIX):
        token = token[len(_ENC_PREFIX) :]
    try:
        raw = _fernet(secret).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise AppError(
            "connector.credentials_unreadable", "连接凭证无法解密"
        ) from exc
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}
