"""common.security:密钥派生兼容、加解密、限流与内部密钥依赖。"""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from typing import Annotated

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from common.errors import AppError
from common.middleware import install_app_error_handler
from common.security import (
    SecretBox,
    SlidingWindowLimiter,
    derive_key,
    require_internal_secret,
)


def _legacy_key(secret: str) -> bytes:
    """R-25 之前各服务本地实现的派生规则(必须保持兼容)。"""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def test_derive_key_matches_legacy_rule() -> None:
    assert derive_key("s3cret") == _legacy_key("s3cret")


def test_decrypts_ciphertext_from_legacy_implementation() -> None:
    legacy = Fernet(_legacy_key("s3cret")).encrypt(b"old-value").decode()
    assert SecretBox("s3cret").decrypt(legacy) == "old-value"


def test_wrong_secret_raises_configured_error() -> None:
    token = SecretBox("s3cret").encrypt("value")
    with pytest.raises(AppError) as excinfo:
        SecretBox("other", error_code="byok.decrypt_failed").decrypt(token)
    assert excinfo.value.code == "byok.decrypt_failed"


def test_json_roundtrip() -> None:
    box = SecretBox("s3cret")
    payload = {"a": 1, "b": "中文"}
    assert box.decrypt_json(box.encrypt_json(payload)) == payload


async def test_sliding_window_limits_and_isolates_keys() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
    assert await limiter.allow("k") is True
    assert await limiter.allow("k") is True
    assert await limiter.allow("k") is False
    assert await limiter.allow("other") is True


def test_internal_secret_dependency_enforced() -> None:
    app = FastAPI()
    app.state.settings = SimpleNamespace(internal_secret="s3cret")
    install_app_error_handler(app)

    @app.get("/internal")
    async def endpoint(_: Annotated[None, Depends(require_internal_secret)]) -> dict:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/internal").status_code == 401
    wrong = {"X-Internal-Secret": "wrong"}
    assert client.get("/internal", headers=wrong).status_code == 401
    ok = {"X-Internal-Secret": "s3cret"}
    assert client.get("/internal", headers=ok).status_code == 200
