"""用量聚合与 BYOK 存取(M0-11 本地版 + BYOK 信封 v2/KMS)。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from common.errors import StorageError
from common.security import SecretBox
from fastapi.testclient import TestClient

from account.main import create_app
from account.services import insights as insights_service
from tests.conftest import _IDP, OTHER, TENANT, _settings

INTERNAL = {
    **TENANT,
    "X-Internal-Secret": "internal-secret",
    "X-Tenant-Id": "org-1",
}
INTERNAL_OTHER = {
    **OTHER,
    "X-Internal-Secret": "internal-secret",
    "X-Tenant-Id": "org-2",
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
    tenant_id: str = "org-1",
) -> None:
    """直接写库:模拟升级前的存量密文(裸 Fernet token 等)。"""
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            "INSERT INTO byok_keys (id, tenant_id, provider, encrypted_key, key_hint)"
            " VALUES (?, ?, ?, ?, ?)",
            (uuid4().hex, tenant_id, provider, encrypted_key, key_hint),
        )


def test_usage_summary_empty_and_recorded(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/summary", headers=TENANT)
        assert empty.status_code == 200
        assert empty.json() == {
            "today_tokens": 0,
            "month_tokens": 0,
            "today_events": 0,
            "month_events": 0,
            "by_model": [],
        }

        recorded = client.post(
            "/v1/usage/events",
            json={
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            headers=INTERNAL,
        )
        assert recorded.status_code == 201

        summary = client.get("/v1/usage/summary", headers=TENANT).json()
        assert summary["today_tokens"] == 15
        assert summary["month_tokens"] == 15
        assert summary["today_events"] == 1
        assert summary["month_events"] == 1
        assert summary["by_model"] == [
            {"model": "deepseek/chat", "tokens": 15, "events": 1}
        ]


def test_usage_internal_secret_required(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 1, "completion_tokens": 1},
            headers=TENANT,
        )
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


def test_byok_upsert_mask_list_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=TENANT,
        )
        assert created.status_code == 200
        body = created.json()
        assert body["provider"] == "openai"
        assert body["masked_key"] == "••••5678"
        assert "sk-secret" not in str(body)

        listing = client.get("/v1/byok", headers=TENANT).json()
        assert [item["provider"] for item in listing] == ["openai"]
        # 跨租户不可见
        assert client.get("/v1/byok", headers=OTHER).json() == []

        updated = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-updated-00009999"},
            headers=TENANT,
        )
        assert updated.json()["masked_key"] == "••••9999"

        assert client.delete("/v1/byok/openai", headers=TENANT).status_code == 204
        assert client.get("/v1/byok", headers=TENANT).json() == []


def test_usage_quota_snapshot(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 未配置配额:三个维度都是未限制,已用量 0
        empty = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
        assert empty.status_code == 200
        assert empty.json() == {
            "monthly_tokens": None,
            "max_concurrent_runs": None,
            "used_tokens": 0,
        }

        updated = client.patch(
            "/v1/organization",
            json={"quota": {"monthly_tokens": 100, "max_concurrent_runs": 2}},
            headers=TENANT,
        )
        assert updated.status_code == 200
        client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 30, "completion_tokens": 10},
            headers=INTERNAL,
        )
        snapshot = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
    assert snapshot.status_code == 200
    assert snapshot.json() == {
        "monthly_tokens": 100,
        "max_concurrent_runs": 2,
        "used_tokens": 40,
    }


def test_usage_quota_requires_internal_secret(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/usage/quota", headers=TENANT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


# ── BYOK 内部解析/轮换端点 + 信封 v2 落库 ─────────────────────────────────


def test_byok_secret_internal_resolve(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=TENANT,
        )
        assert created.status_code == 200

        response = client.get("/v1/byok/openai/secret", headers=INTERNAL)
        assert response.status_code == 200
        # 明文只在内部解析端点出现,且禁止任何中间层缓存
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "provider": "openai",
            "api_key": "sk-secret-12345678",
            "key_hint": "5678",
            "key_id": "local-1",
        }

        # 跨租户内部调用等价于不存在(不泄漏存在性)
        cross = client.get("/v1/byok/openai/secret", headers=INTERNAL_OTHER)
        assert cross.status_code == 404
        assert cross.json()["code"] == "byok.not_found"


def test_byok_secret_requires_internal_auth(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=TENANT,
        )
        no_secret = client.get("/v1/byok/openai/secret", headers=TENANT)
        no_tenant = client.get(
            "/v1/byok/openai/secret",
            headers={**TENANT, "X-Internal-Secret": "internal-secret"},
        )
    assert no_secret.status_code == 401
    assert no_secret.json()["code"] == "auth.internal_forbidden"
    assert no_tenant.status_code == 401
    assert no_tenant.json()["code"] == "auth.tenant_missing"


def test_byok_secret_missing_provider(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/byok/nope/secret", headers=INTERNAL)
    assert response.status_code == 404
    assert response.json()["code"] == "byok.not_found"


def test_byok_put_stores_v2_envelope(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=TENANT,
        )
        stored = _stored_keys(tmp_path)["openai"]
    envelope = json.loads(stored)
    assert envelope["v"] == 2
    assert envelope["kms"] == "local"
    assert envelope["kid"] == "local-1"
    assert "sk-secret" not in stored


def test_byok_legacy_token_lazily_rotated(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_byok(
            tmp_path,
            "anthropic",
            SecretBox("byok-secret").encrypt("sk-legacy-00001111"),
            key_hint="1111",
        )
        response = client.get("/v1/byok/anthropic/secret", headers=INTERNAL)
        assert response.status_code == 200
        assert response.json()["api_key"] == "sk-legacy-00001111"
    stored = _stored_keys(tmp_path)["anthropic"]
    payload = json.loads(stored)
    assert payload["v"] == 2
    assert payload["kid"] == "local-1"
    assert "sk-legacy" not in stored


def test_byok_rotate_endpoint(tmp_path: Path) -> None:
    legacy = SecretBox("byok-secret")
    with _client(tmp_path) as client:
        _seed_byok(tmp_path, "openai", legacy.encrypt("sk-openai-1"), key_hint="n001")
        _seed_byok(
            tmp_path, "anthropic", legacy.encrypt("sk-anthropic-1"), key_hint="n002"
        )

        result = client.post("/v1/byok/rotate", headers=INTERNAL)
        assert result.status_code == 200
        assert result.json() == {"rotated": 2, "failed": 0}
        # 租户隔离:别的租户没有 key,统计为 0 而不是报错
        other = client.post("/v1/byok/rotate", headers=INTERNAL_OTHER)
        assert other.json() == {"rotated": 0, "failed": 0}

        # 轮换后仍能解析出同一份明文
        assert (
            client.get("/v1/byok/openai/secret", headers=INTERNAL).json()["api_key"]
            == "sk-openai-1"
        )
        assert (
            client.get("/v1/byok/anthropic/secret", headers=INTERNAL).json()["api_key"]
            == "sk-anthropic-1"
        )

    versions = {
        provider: json.loads(value)["v"]
        for provider, value in _stored_keys(tmp_path).items()
    }
    assert versions == {"anthropic": 2, "openai": 2}


def test_byok_rotate_requires_internal_secret(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post("/v1/byok/rotate", headers=TENANT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


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


def test_byok_lazy_rotate_failure_still_returns_secret(
    tmp_path: Path, monkeypatch
) -> None:
    log = _RecordingLog()
    monkeypatch.setattr(insights_service, "log", log)
    with _client(tmp_path, byok_kms=_RotateBrokenKms()) as client:
        _seed_byok(tmp_path, "anthropic", "opaque-ciphertext")
        response = client.get("/v1/byok/anthropic/secret", headers=INTERNAL)
        assert response.status_code == 200
        assert response.json()["api_key"] == "sk-legacy-7777"

    warnings = [fields for event, fields in log.calls if event == "byok.rotate_failed"]
    assert [fields["code"] for fields in warnings] == ["byok.unreadable"]
    dumped = json.dumps(log.calls, ensure_ascii=False)
    assert "sk-legacy-7777" not in dumped


def test_byok_rotate_reports_failures(tmp_path: Path) -> None:
    with _client(tmp_path, byok_kms=_PartialRotateKms()) as client:
        _seed_byok(tmp_path, "openai", "ok-cipher")
        _seed_byok(tmp_path, "anthropic", "fail-me")
        result = client.post("/v1/byok/rotate", headers=INTERNAL)
        assert result.status_code == 200
        assert result.json() == {"rotated": 1, "failed": 1}

    stored = _stored_keys(tmp_path)
    assert stored["openai"] == "rotated-ok"
    # 失败的条目保持原样,不静默丢
    assert stored["anthropic"] == "fail-me"


def test_byok_logs_never_contain_plaintext(tmp_path: Path, monkeypatch) -> None:
    log = _RecordingLog()
    monkeypatch.setattr(insights_service, "log", log)
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-super-secret-abcdef"},
            headers=TENANT,
        )
        assert client.get("/v1/byok/openai/secret", headers=INTERNAL).status_code == 200
        assert client.post("/v1/byok/rotate", headers=INTERNAL).status_code == 200

    dumped = json.dumps(log.calls, ensure_ascii=False)
    assert "sk-super-secret-abcdef" not in dumped
    rotated = next(fields for event, fields in log.calls if event == "byok.rotated")
    assert rotated == {"tenant_id": "org-1", "key_id": "local-1", "rotated": 1}
