"""BYOK 存取:基础 CRUD、内部解析端点与信封 v2/KMS 轮换。"""

from __future__ import annotations

import json
from pathlib import Path

from common.security import SecretBox

from account.services import insights as insights_service
from tests.conftest import ACCOUNT, ACCOUNT_ID, OTHER_ACCOUNT
from tests.insights_support import (
    INTERNAL,
    INTERNAL_OTHER,
    _client,
    _PartialRotateKms,
    _RecordingLog,
    _RotateBrokenKms,
    _seed_byok,
    _stored_keys,
)

# ── BYOK 基础 CRUD ─────────────────────────────────────────────────────


def test_byok_upsert_mask_list_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
        )
        assert created.status_code == 200
        body = created.json()
        assert body["provider"] == "openai"
        assert body["masked_key"] == "••••5678"
        assert "sk-secret" not in str(body)

        listing = client.get("/v1/byok", headers=ACCOUNT).json()
        assert [item["provider"] for item in listing] == ["openai"]
        # 跨账号不可见
        assert client.get("/v1/byok", headers=OTHER_ACCOUNT).json() == []

        updated = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-updated-00009999"},
            headers=ACCOUNT,
        )
        assert updated.json()["masked_key"] == "••••9999"

        assert client.delete("/v1/byok/openai", headers=ACCOUNT).status_code == 204
        assert client.get("/v1/byok", headers=ACCOUNT).json() == []


# ── BYOK 内部解析/轮换端点 + 信封 v2 落库 ─────────────────────────────────


def test_byok_secret_internal_resolve(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
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

        # 跨账号内部调用等价于不存在(不泄漏存在性)
        cross = client.get("/v1/byok/openai/secret", headers=INTERNAL_OTHER)
        assert cross.status_code == 404
        assert cross.json()["code"] == "byok.not_found"


def test_byok_secret_requires_internal_auth(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
        )
        no_secret = client.get("/v1/byok/openai/secret", headers=ACCOUNT)
        no_account = client.get(
            "/v1/byok/openai/secret",
            headers={**ACCOUNT, "X-Internal-Secret": "internal-secret"},
        )
    assert no_secret.status_code == 401
    assert no_secret.json()["code"] == "auth.internal_forbidden"
    assert no_account.status_code == 401
    assert no_account.json()["code"] == "auth.account_missing"


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
            headers=ACCOUNT,
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
        # 账号隔离:别的账号没有 key,统计为 0 而不是报错
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
        response = client.post("/v1/byok/rotate", headers=ACCOUNT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


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
            headers=ACCOUNT,
        )
        assert client.get("/v1/byok/openai/secret", headers=INTERNAL).status_code == 200
        assert client.post("/v1/byok/rotate", headers=INTERNAL).status_code == 200

    dumped = json.dumps(log.calls, ensure_ascii=False)
    assert "sk-super-secret-abcdef" not in dumped
    rotated = next(fields for event, fields in log.calls if event == "byok.rotated")
    assert rotated == {"account_id": ACCOUNT_ID, "key_id": "local-1", "rotated": 1}
