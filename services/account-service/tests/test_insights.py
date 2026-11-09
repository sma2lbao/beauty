"""用量聚合与 BYOK 存取(M0-11 本地版 + BYOK 信封 v2/KMS)。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from common.errors import StorageError
from common.security import SecretBox
from fastapi.testclient import TestClient

from account.main import create_app
from account.services import insights as insights_service
from tests.conftest import (
    _IDP,
    ACCOUNT,
    ACCOUNT_ID,
    OTHER_ACCOUNT,
    OTHER_ACCOUNT_ID,
    _settings,
)

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


def test_usage_summary_empty_and_recorded(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/summary", headers=ACCOUNT)
        assert empty.status_code == 200
        assert empty.json() == {
            "today_tokens": 0,
            "month_tokens": 0,
            "today_events": 0,
            "month_events": 0,
            "today_cost_usd": 0.0,
            "month_cost_usd": 0.0,
            "unpriced_events": 0,
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

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
        assert summary["today_tokens"] == 15
        assert summary["month_tokens"] == 15
        assert summary["today_events"] == 1
        assert summary["month_events"] == 1
        assert summary["today_cost_usd"] == 0.0
        assert summary["month_cost_usd"] == 0.0
        # 这条上报没带 cost_usd → 未计价,必须计数而不是冒充 0 元
        assert summary["unpriced_events"] == 1
        assert summary["by_model"] == [
            {
                "model": "deepseek/chat",
                "tokens": 15,
                "events": 1,
                "cost_usd": 0.0,
                "unpriced_events": 1,
            }
        ]


def test_usage_summary_aggregates_cost_and_billing_source(tmp_path: Path) -> None:
    """P0-1:金额按模型聚合,平台/BYOK 分别记账;未计价与 0 元不混淆。"""
    with _client(tmp_path) as client:
        for body in (
            {
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": 0.0012,
                "billing_source": "platform",
            },
            {
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "cost_usd": 0.0034,
                "billing_source": "platform",
            },
            {
                "run_id": "r2",
                "model": "openai/custom-model",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "billing_source": "byok",
            },
        ):
            resp = client.post("/v1/usage/events", json=body, headers=INTERNAL)
            assert resp.status_code == 201

        assert (
            client.post(
                "/v1/usage/events",
                json={"model": "m", "cost_usd": -1},
                headers=INTERNAL,
            ).status_code
            == 422
        )

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()

    assert summary["month_tokens"] == 47
    assert summary["month_cost_usd"] == pytest.approx(0.0046)
    assert summary["today_cost_usd"] == pytest.approx(0.0046)
    assert summary["unpriced_events"] == 1
    by_model = {entry["model"]: entry for entry in summary["by_model"]}
    assert by_model["deepseek/chat"] == {
        "model": "deepseek/chat",
        "tokens": 45,
        "events": 2,
        "cost_usd": pytest.approx(0.0046),
        "unpriced_events": 0,
    }
    assert by_model["openai/custom-model"] == {
        "model": "openai/custom-model",
        "tokens": 2,
        "events": 1,
        "cost_usd": 0.0,
        "unpriced_events": 1,
    }


def test_usage_event_id_is_idempotent(tmp_path: Path) -> None:
    """P0-3:outbox 至少一次投递 → 同一 event_id 只落一条、只计费一次。"""
    body = {
        "event_id": "b" * 32 + ":0",
        "run_id": "r1",
        "model": "deepseek/chat",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": 0.0012,
    }
    with _client(tmp_path) as client:
        first = client.post("/v1/usage/events", json=body, headers=INTERNAL)
        second = client.post("/v1/usage/events", json=body, headers=INTERNAL)
        assert first.status_code == 201
        assert second.status_code == 201
        # 幂等命中返回已落库那条的 id,不新增行
        assert second.json()["id"] == first.json()["id"]
        # 账号隔离:另一个账号可以有自己的同 event_id
        other = client.post("/v1/usage/events", json=body, headers=INTERNAL_OTHER)
        assert other.status_code == 201
        assert other.json()["id"] != first.json()["id"]

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
    assert summary["month_events"] == 1
    assert summary["month_tokens"] == 15
    assert summary["month_cost_usd"] == pytest.approx(0.0012)


def test_usage_event_id_optional_and_bounded(tmp_path: Path) -> None:
    """旧直连调用不传 event_id 仍然能落库(空值不参与唯一约束)。"""
    with _client(tmp_path) as client:
        body = {"model": "m", "prompt_tokens": 1, "completion_tokens": 1}
        assert (
            client.post("/v1/usage/events", json=body, headers=INTERNAL).status_code
            == 201
        )
        assert (
            client.post("/v1/usage/events", json=body, headers=INTERNAL).status_code
            == 201
        )
        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
        assert summary["month_events"] == 2

        too_long = client.post(
            "/v1/usage/events",
            json={**body, "event_id": "x" * 65},
            headers=INTERNAL,
        )
        assert too_long.status_code == 422


def test_usage_internal_secret_required(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 1, "completion_tokens": 1},
            headers=ACCOUNT,
        )
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


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


def test_usage_quota_snapshot(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 账号不存在 / quota 为空:三个维度都是未限制(fail-open),已用量 0
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

        # 配额落在账号行(users.quota),由服务端维护,不再有组织端点可改
        with sqlite3.connect(tmp_path / "test.db") as conn:
            conn.execute(
                "INSERT INTO users (id, subject, email, display_name, status, quota)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ACCOUNT_ID,
                    "acc-1@example.com",
                    "acc-1@example.com",
                    "acc-1",
                    "active",
                    json.dumps({"monthly_tokens": 100, "max_concurrent_runs": 2}),
                ),
            )
            conn.commit()

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
        response = client.get("/v1/usage/quota", headers=ACCOUNT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


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
            headers=ACCOUNT,
        )
        assert client.get("/v1/byok/openai/secret", headers=INTERNAL).status_code == 200
        assert client.post("/v1/byok/rotate", headers=INTERNAL).status_code == 200

    dumped = json.dumps(log.calls, ensure_ascii=False)
    assert "sk-super-secret-abcdef" not in dumped
    rotated = next(fields for event, fields in log.calls if event == "byok.rotated")
    assert rotated == {"account_id": ACCOUNT_ID, "key_id": "local-1", "rotated": 1}


# ── BYOK 自定义模型清单(models 列)与内部 catalog 端点 ──────────────────────


def test_byok_upsert_models_normalized_and_listed(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={
                "api_key": "sk-secret-12345678",
                "models": [
                    "openai/gpt-4o-mini",
                    " openai/gpt-4o ",
                    "",
                    "   ",
                    "openai/gpt-4o-mini",
                ],
            },
            headers=ACCOUNT,
        )
        assert created.status_code == 200
        assert created.json()["models"] == ["openai/gpt-4o-mini", "openai/gpt-4o"]

        listing = client.get("/v1/byok", headers=ACCOUNT).json()
        assert listing[0]["models"] == ["openai/gpt-4o-mini", "openai/gpt-4o"]
        assert _stored_models(tmp_path) == ["openai/gpt-4o-mini", "openai/gpt-4o"]


def test_byok_models_omitted_clears_previous_list(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678", "models": ["openai/gpt-4o"]},
            headers=ACCOUNT,
        )
        replaced = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678", "models": ["openai/o3-mini"]},
            headers=ACCOUNT,
        )
        assert replaced.json()["models"] == ["openai/o3-mini"]

        # 省略 models = 整体替换为空清单
        cleared = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
        )
    assert cleared.json()["models"] == []
    assert _stored_models(tmp_path) == []


def test_byok_models_only_update_keeps_key(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
        )
        assert created.json()["key_hint"] == "5678"
        before = _stored_keys(tmp_path)["openai"]

        # 省略 / null / 空串三种写法都表示「不换 key」,只更新模型清单
        for body in (
            {"models": ["openai/gpt-4o"]},
            {"api_key": None, "models": ["openai/gpt-4o", "openai/o3-mini"]},
            {"api_key": "", "models": ["openai/gpt-4o"]},
            {"api_key": "   ", "models": []},
        ):
            updated = client.put("/v1/byok/openai", json=body, headers=ACCOUNT)
            assert updated.status_code == 200
            assert updated.json()["key_hint"] == "5678"
            assert updated.json()["masked_key"] == "••••5678"
            # 密文逐字节不变(没有重新加密)
            assert _stored_keys(tmp_path)["openai"] == before

        # 明文仍是原来那把 key
        secret = client.get("/v1/byok/openai/secret", headers=INTERNAL)
    assert secret.json()["api_key"] == "sk-secret-12345678"
    assert _stored_models(tmp_path) == []


def test_byok_first_save_without_api_key_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        for body in (
            {"models": ["openai/gpt-4o"]},
            {"api_key": None, "models": ["openai/gpt-4o"]},
            {"api_key": ""},
            {"api_key": "   "},
        ):
            response = client.put("/v1/byok/openai", json=body, headers=ACCOUNT)
            assert response.status_code == 400
            assert response.json()["code"] == "byok.invalid_key"
        # 没有落库任何记录
        assert client.get("/v1/byok", headers=ACCOUNT).json() == []


def test_byok_provider_invalid_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        for provider in ("-bad", "bad provider", "a" * 33, "bad!provider"):
            response = client.put(
                f"/v1/byok/{provider}",
                json={"api_key": "sk-secret-12345678"},
                headers=ACCOUNT,
            )
            assert response.status_code == 400, provider
            assert response.json()["code"] == "byok.invalid_provider"


def test_byok_provider_normalized(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/OpenAI",
            json={"api_key": "sk-secret-12345678"},
            headers=ACCOUNT,
        )
        assert created.status_code == 200
        assert created.json()["provider"] == "openai"
        # 归一后与已存的 openai 是同一条记录
        again = client.put(
            "/v1/byok/ openai ",
            json={"api_key": "sk-secret-00009999"},
            headers=ACCOUNT,
        )
        assert again.json()["provider"] == "openai"
        assert [
            item["provider"] for item in client.get("/v1/byok", headers=ACCOUNT).json()
        ] == ["openai"]


def test_byok_invalid_model_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        cases = [
            ["gpt-4o-mini"],  # 缺 provider 前缀
            ["anthropic/claude-3-5-sonnet"],  # 前缀与 provider 不一致
            ["openai/gpt 4o"],  # 字符集非法(空格)
            [
                "openai/gpt-4o!",
            ],  # 字符集非法(!)
            ["openai/" + "a" * 122],  # 129 字符,超 128
        ]
        for models in cases:
            response = client.put(
                "/v1/byok/openai",
                json={"api_key": "sk-secret-12345678", "models": models},
                headers=ACCOUNT,
            )
            assert response.status_code == 400, models
            assert response.json()["code"] == "byok.invalid_model"

        # 128 字符边界合法
        boundary = "openai/" + "a" * 121
        assert len(boundary) == 128
        ok = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678", "models": [boundary]},
            headers=ACCOUNT,
        )
    assert ok.status_code == 200
    assert ok.json()["models"] == [boundary]


def test_byok_model_count_limit(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        too_many = [f"openai/m{i}" for i in range(33)]
        rejected = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678", "models": too_many},
            headers=ACCOUNT,
        )
        assert rejected.status_code == 400
        assert rejected.json()["code"] == "byok.invalid_model"

        # 去重后 <=32 条则通过(重复项不计数)
        allowed = client.put(
            "/v1/byok/openai",
            json={
                "api_key": "sk-secret-12345678",
                "models": [*too_many[:32], too_many[0]],
            },
            headers=ACCOUNT,
        )
    assert allowed.status_code == 200
    assert allowed.json()["models"] == too_many[:32]


def test_byok_catalog_union_and_account_isolation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.put(
            "/v1/byok/openai",
            json={
                "api_key": "sk-openai-12345678",
                "models": ["openai/gpt-4o-mini", "openai/gpt-4o"],
            },
            headers=ACCOUNT,
        )
        client.put(
            "/v1/byok/anthropic",
            json={
                "api_key": "sk-anthropic-12345678",
                "models": ["anthropic/claude-3-5-sonnet"],
            },
            headers=ACCOUNT,
        )
        # 另一个账号的自定义模型不应出现在本账号 catalog 中
        client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-other-12345678", "models": ["openai/o3-mini"]},
            headers=OTHER_ACCOUNT,
        )

        catalog = client.get("/v1/byok/catalog", headers=INTERNAL)
        assert catalog.status_code == 200
        assert catalog.headers["cache-control"] == "no-store"
        # 按 provider 升序展开各自 models(anthropic 在 openai 之前)
        assert catalog.json() == {
            "models": [
                "anthropic/claude-3-5-sonnet",
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
            ]
        }

        other = client.get("/v1/byok/catalog", headers=INTERNAL_OTHER)
        assert other.json() == {"models": ["openai/o3-mini"]}

        # 未配置 BYOK 的账号:空并集而不是 404
        empty = client.get(
            "/v1/byok/catalog",
            headers={**INTERNAL, "X-Account-Id": "acc-999"},
        )
    assert empty.status_code == 200
    assert empty.json() == {"models": []}


def test_byok_catalog_dedupes_seeded_duplicates(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 存量/异常行:同一模型在记录内与跨记录重复,并集必须去重保序
        _seed_byok(
            tmp_path,
            "openai",
            "cipher-1",
            models=["openai/gpt-4o", "openai/gpt-4o"],
        )
        _seed_byok(
            tmp_path,
            "anthropic",
            "cipher-2",
            models=["openai/gpt-4o", "anthropic/claude"],
        )
        catalog = client.get("/v1/byok/catalog", headers=INTERNAL)
    assert catalog.json() == {"models": ["openai/gpt-4o", "anthropic/claude"]}


def test_byok_catalog_requires_internal_auth(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        no_secret = client.get("/v1/byok/catalog", headers=ACCOUNT)
        no_account = client.get(
            "/v1/byok/catalog",
            headers={**ACCOUNT, "X-Internal-Secret": "internal-secret"},
        )
    assert no_secret.status_code == 401
    assert no_secret.json()["code"] == "auth.internal_forbidden"
    assert no_account.status_code == 401
    assert no_account.json()["code"] == "auth.account_missing"


def test_usage_quota_self_read_with_session_auth(tmp_path: Path) -> None:
    """P0-2:前端配额条走会话鉴权读自己的快照,内部端点仍是共享密钥。"""
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/quota/me", headers=ACCOUNT)
        assert empty.status_code == 200
        assert empty.json() == {
            "monthly_tokens": None,
            "max_concurrent_runs": None,
            "used_tokens": 0,
        }

        with sqlite3.connect(tmp_path / "test.db") as conn:
            conn.execute(
                "INSERT INTO users (id, subject, email, display_name, status, quota)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ACCOUNT_ID,
                    "acc-1@example.com",
                    "acc-1@example.com",
                    "acc-1",
                    "active",
                    json.dumps({"monthly_tokens": 100, "max_concurrent_runs": 2}),
                ),
            )
            conn.commit()

        client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 30, "completion_tokens": 10},
            headers=INTERNAL,
        )

        snapshot = client.get("/v1/usage/quota/me", headers=ACCOUNT)
        internal = client.get("/v1/usage/quota", headers=INTERNAL)

    assert snapshot.status_code == 200
    assert snapshot.json() == internal.json()
    assert snapshot.json() == {
        "monthly_tokens": 100,
        "max_concurrent_runs": 2,
        "used_tokens": 40,
    }


def test_usage_quota_self_read_requires_session(tmp_path: Path) -> None:
    """没有会话/内部密钥就不给读配额(两端点都拒绝)。"""
    with _client(tmp_path) as client:
        anonymous = client.get("/v1/usage/quota/me")
        internal_only = client.get("/v1/usage/quota/me", headers=INTERNAL)
    assert anonymous.status_code == 401
    assert internal_only.status_code == 401
