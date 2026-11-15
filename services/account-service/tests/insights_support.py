"""insights 测试共享装配:内部端点头、客户端工厂与 BYOK 直写库助手。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from common.errors import StorageError
from fastapi.testclient import TestClient

from account.main import create_app
from tests.conftest import _IDP, ACCOUNT_ID, OTHER_ACCOUNT_ID, _settings

# 内部端点:共享密钥 + 显式 X-Account-Id(不认用户 token)
INTERNAL = {
    "X-Internal-Secret": "internal-secret",
    "X-Account-Id": ACCOUNT_ID,
}
INTERNAL_OTHER = {
    "X-Internal-Secret": "internal-secret",
    "X-Account-Id": OTHER_ACCOUNT_ID,
}


def _client(tmp_path: Path, **app_kwargs) -> TestClient:
    app = create_app(
        _settings(
            tmp_path,
            internal_secret="internal-secret",
            byok_secret="byok-secret",
        ),
        token_verifier=_IDP.verifier(),
        **app_kwargs,
    )
    return TestClient(app)


def _stored_keys(tmp_path: Path) -> dict[str, str]:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        rows = conn.execute(
            "SELECT provider, encrypted_key FROM byok_keys ORDER BY provider"
        ).fetchall()
    return dict(rows)


def _seed_byok(
    tmp_path: Path,
    provider: str,
    encrypted_key: str,
    *,
    key_hint: str = "1234",
    account_id: str = ACCOUNT_ID,
    models: list[str] | None = None,
) -> None:
    """直接写库:模拟升级前的存量密文(裸 Fernet token 等)。"""
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            "INSERT INTO byok_keys"
            " (id, account_id, provider, encrypted_key, key_hint, models)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                account_id,
                provider,
                encrypted_key,
                key_hint,
                json.dumps(models or []),
            ),
        )


def _stored_models(
    tmp_path: Path, provider: str = "openai", account_id: str = ACCOUNT_ID
) -> list[str]:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        row = conn.execute(
            "SELECT models FROM byok_keys WHERE account_id = ? AND provider = ?",
            (account_id, provider),
        ).fetchone()
    return json.loads(row[0]) if row is not None else []


class _RotateBrokenKms:
    """decrypt 正常、rotate 必失败的桩 KMS:验证懒轮换失败不阻断取用。"""

    name = "local"
    key_id = "local-1"

    async def encrypt(self, plaintext: str) -> str:
        return plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return "sk-legacy-7777"

    def needs_rotation(self, ciphertext: str) -> bool:
        return True

    async def rotate(self, ciphertext: str) -> str:
        raise StorageError("byok.unreadable", "BYOK 密钥无法解密")

    async def aclose(self) -> None:
        return None


class _PartialRotateKms(_RotateBrokenKms):
    async def rotate(self, ciphertext: str) -> str:
        if ciphertext == "fail-me":
            raise StorageError("byok.unreadable", "BYOK 密钥无法解密")
        return "rotated-ok"


class _RecordingLog:
    """记录日志事件与字段的结构化日志替身(断言不泄漏明文)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.calls.append((event, fields))

    def warning(self, event: str, **fields) -> None:
        self.calls.append((event, fields))
