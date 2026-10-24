"""连接器凭证信封:加解密委托 common.security,信封格式与历史明文回退留在本模块。"""

from __future__ import annotations

import json
from typing import Any

from common.security import SecretBox

_ENC_PREFIX = "fernet:"
_ERROR_CODE = "connector.credentials_unreadable"
_ERROR_MESSAGE = "连接凭证无法解密"


def _box(secret: str) -> SecretBox:
    return SecretBox(secret, error_code=_ERROR_CODE, error_message=_ERROR_MESSAGE)


def encrypt_credentials(secret: str, credentials: dict[str, Any]) -> dict[str, str]:
    return {
        "v": "1",
        "ciphertext": _ENC_PREFIX + _box(secret).encrypt_json(credentials),
    }


def decrypt_credentials(secret: str, stored: dict[str, Any]) -> dict[str, Any]:
    """兼容历史明文行:没有 ciphertext 字段时按明文返回。"""
    ciphertext = stored.get("ciphertext") if isinstance(stored, dict) else None
    if not ciphertext:
        return dict(stored or {})
    token = str(ciphertext)
    if token.startswith(_ENC_PREFIX):
        token = token[len(_ENC_PREFIX) :]
    data = json.loads(_box(secret).decrypt(token))
    return data if isinstance(data, dict) else {}
