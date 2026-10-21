"""凭证信封加密:连接器框架的留存能力(读端只进不出)。"""

from __future__ import annotations

import pytest
from common.errors import AppError

from connector.core.crypto import decrypt_credentials, encrypt_credentials

SECRET = "unit-test-secret"


def test_roundtrip_encrypts_plaintext() -> None:
    credentials = {"refresh_token": "refresh-1", "email": "me@gmail.com"}
    stored = encrypt_credentials(SECRET, credentials)
    assert "refresh-1" not in str(stored)
    assert stored["ciphertext"].startswith("fernet:")
    assert decrypt_credentials(SECRET, stored) == credentials


def test_wrong_secret_rejected() -> None:
    stored = encrypt_credentials(SECRET, {"access_token": "access-1"})
    with pytest.raises(AppError):
        decrypt_credentials("another-secret", stored)


def test_legacy_plaintext_still_readable() -> None:
    """历史明文行(无 ciphertext 字段)按明文返回,供迁移期读取。"""
    stored = {"refresh_token": "legacy-1"}
    assert decrypt_credentials(SECRET, stored) == stored
