"""BYOK 自定义模型清单(models 列)与内部 catalog 端点。"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import ACCOUNT, OTHER_ACCOUNT
from tests.insights_support import (
    INTERNAL,
    INTERNAL_OTHER,
    _client,
    _seed_byok,
    _stored_keys,
    _stored_models,
)

# ── 模型清单校验与归一 ─────────────────────────────────────────────────


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


# ── 内部 catalog:并集/去重/鉴权 ─────────────────────────────────────────


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
