"""BYOK KMS 抽象:local(Fernet+信封 v2)与 Vault Transit(全程 MockTransport,零网络)。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from common.errors import AppError, StorageError, UpstreamError
from common.security import SecretBox

from account.core.byok_kms import LocalByokKms, VaultByokKms, build_byok_kms
from account.core.config import Settings
from tests.conftest import _settings


def _envelope(ciphertext: str) -> dict:
    return json.loads(ciphertext)


def _legacy(secret: str, plaintext: str) -> str:
    """v2 之前的裸 Fernet token(历史密文)。"""
    return SecretBox(secret).encrypt(plaintext)


# ── local:信封往返 / 历史 token / 历史 kid / 轮换 ──────────────────────────


async def test_local_roundtrip_envelope() -> None:
    kms = LocalByokKms("secret-1", key_id="k1")
    ciphertext = await kms.encrypt("sk-roundtrip")

    envelope = _envelope(ciphertext)
    assert envelope["v"] == 2
    assert envelope["kms"] == "local"
    assert envelope["kid"] == "k1"
    assert "sk-roundtrip" not in ciphertext

    assert await kms.decrypt(ciphertext) == "sk-roundtrip"
    assert kms.needs_rotation(ciphertext) is False
    await kms.aclose()


async def test_local_legacy_token_decrypts_and_rotates() -> None:
    kms = LocalByokKms("secret-1", key_id="k1")
    legacy = _legacy("secret-1", "sk-legacy")

    # 历史裸 token 仍可解(否则存量用户升级后全部失效)
    assert await kms.decrypt(legacy) == "sk-legacy"
    assert kms.needs_rotation(legacy) is True

    rotated = await kms.rotate(legacy)
    assert _envelope(rotated)["v"] == 2
    assert _envelope(rotated)["kid"] == "k1"
    assert kms.needs_rotation(rotated) is False
    assert await kms.decrypt(rotated) == "sk-legacy"


async def test_local_previous_kid_decrypts() -> None:
    old = LocalByokKms("old-secret", key_id="k0")
    ciphertext = await old.encrypt("sk-old")

    new = LocalByokKms("new-secret", key_id="k1", previous={"k0": "old-secret"})
    assert await new.decrypt(ciphertext) == "sk-old"
    assert new.needs_rotation(ciphertext) is True

    rotated = await new.rotate(ciphertext)
    assert _envelope(rotated)["kid"] == "k1"
    assert await new.decrypt(rotated) == "sk-old"


async def test_local_unknown_kid_and_wrong_key_unreadable() -> None:
    stranger = await LocalByokKms("other-secret", key_id="zz").encrypt("sk-x")
    kms = LocalByokKms("secret-1", key_id="k1")

    with pytest.raises(StorageError) as unknown_kid:
        await kms.decrypt(stranger)
    assert unknown_kid.value.code == "byok.unreadable"
    assert unknown_kid.value.status == 500

    legacy = _legacy("wrong-secret", "sk-y")
    with pytest.raises(AppError) as wrong_key:
        await kms.decrypt(legacy)
    assert wrong_key.value.code == "byok.unreadable"


async def test_local_previous_can_be_used_for_legacy_tokens() -> None:
    """裸 token 依次尝试当前/历史密钥:历史密钥加密的存量 key 也能恢复。"""
    old_token = _legacy("old-secret", "sk-from-old")
    kms = LocalByokKms("new-secret", key_id="k1", previous={"k0": "old-secret"})
    assert await kms.decrypt(old_token) == "sk-from-old"
    assert kms.needs_rotation(old_token) is True


# ── Vault Transit:encrypt/decrypt/rewrap + 故障契约 ────────────────────────


def _vault_handler(calls: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/encrypt/byok"):
            payload = json.loads(request.content)
            plaintext = base64.b64decode(payload["plaintext"]).decode()
            return httpx.Response(
                200, json={"data": {"ciphertext": f"vault:v1:{plaintext}"}}
            )
        if path.endswith("/decrypt/byok"):
            payload = json.loads(request.content)
            assert payload["ciphertext"].startswith("vault:v")
            plaintext = payload["ciphertext"].split(":", 2)[2]
            encoded = base64.b64encode(plaintext.encode()).decode()
            return httpx.Response(200, json={"data": {"plaintext": encoded}})
        if path.endswith("/rewrap/byok"):
            payload = json.loads(request.content)
            assert payload["ciphertext"].startswith("vault:v1:")
            plaintext = payload["ciphertext"].split(":", 2)[2]
            return httpx.Response(
                200, json={"data": {"ciphertext": f"vault:v2:{plaintext}"}}
            )
        return httpx.Response(404, json={"errors": ["unexpected path"]})

    return handler


def _vault(calls: list[httpx.Request]) -> VaultByokKms:
    return VaultByokKms(
        url="http://vault.test/",
        token="vault-token",
        mount="transit",
        key="byok",
        transport=httpx.MockTransport(_vault_handler(calls)),
    )


async def test_vault_encrypt_decrypt_rewrap() -> None:
    calls: list[httpx.Request] = []
    kms = _vault(calls)

    ciphertext = await kms.encrypt("sk-vault")
    envelope = _envelope(ciphertext)
    assert envelope["kms"] == "vault"
    assert envelope["kid"] == "byok"
    assert envelope["ct"] == "vault:v1:sk-vault"
    assert kms.needs_rotation(ciphertext) is False

    assert await kms.decrypt(ciphertext) == "sk-vault"
    rotated = await kms.rotate(ciphertext)
    assert _envelope(rotated)["ct"] == "vault:v2:sk-vault"
    assert await kms.decrypt(rotated) == "sk-vault"

    # 地址 /v1/{mount}/{op}/{key},token 走 X-Vault-Token
    assert [call.url.path for call in calls] == [
        "/v1/transit/encrypt/byok",
        "/v1/transit/decrypt/byok",
        "/v1/transit/rewrap/byok",
        "/v1/transit/decrypt/byok",
    ]
    assert all(call.method == "POST" for call in calls)
    assert all(call.headers["X-Vault-Token"] == "vault-token" for call in calls)
    await kms.aclose()


async def test_vault_failures_are_upstream_unavailable() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": ["boom"]})

    kms = VaultByokKms(
        url="http://vault.test",
        token="t",
        transport=httpx.MockTransport(failing),
    )
    with pytest.raises(UpstreamError) as excinfo:
        await kms.encrypt("sk-vault")
    assert excinfo.value.code == "byok.kms_unavailable"
    assert excinfo.value.status == 502
    await kms.aclose()

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("vault down", request=request)

    healthy = VaultByokKms(
        url="http://vault.test",
        token="t",
        transport=httpx.MockTransport(_vault_handler([])),
    )
    ciphertext = await healthy.encrypt("sk-x")
    await healthy.aclose()

    offline_kms = VaultByokKms(
        url="http://vault.test", token="t", transport=httpx.MockTransport(offline)
    )
    with pytest.raises(UpstreamError) as offline_error:
        await offline_kms.decrypt(ciphertext)
    assert offline_error.value.code == "byok.kms_unavailable"
    await offline_kms.aclose()


async def test_vault_cannot_read_legacy_token() -> None:
    calls: list[httpx.Request] = []
    kms = _vault(calls)
    legacy = _legacy("secret-1", "sk-legacy")

    assert kms.needs_rotation(legacy) is True
    with pytest.raises(AppError) as excinfo:
        await kms.decrypt(legacy)
    assert excinfo.value.code == "byok.unreadable"
    with pytest.raises(AppError):
        await kms.rotate(legacy)
    # 无法解的历史密文不会白跑 HTTP
    assert calls == []
    await kms.aclose()


# ── 装配:build_byok_kms 按配置选实现,缺配置/未知取值启动即失败 ────────────


async def test_build_local_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, byok_secret="local-secret", byok_kek_id="local-9")
    kms = build_byok_kms(settings)

    assert isinstance(kms, LocalByokKms)
    assert kms.name == "local"
    assert kms.key_id == "local-9"
    assert _envelope(await kms.encrypt("sk-1"))["kid"] == "local-9"
    await kms.aclose()


async def test_build_local_previous_secrets(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        byok_secret="new-secret",
        byok_kek_id="local-2",
        byok_previous_secrets={"local-1": "old-secret"},
    )
    kms = build_byok_kms(settings)
    assert await kms.decrypt(_legacy("old-secret", "sk-old")) == "sk-old"
    await kms.aclose()


async def test_build_vault(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        byok_kms="vault",
        byok_vault_url="http://vault.test/",
        byok_vault_token="vault-token",
        byok_vault_mount="kv",
        byok_vault_key="myk",
        byok_vault_timeout_seconds=2.5,
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/encrypt/myk"):
            payload = json.loads(request.content)
            plaintext = base64.b64decode(payload["plaintext"]).decode()
            return httpx.Response(
                200, json={"data": {"ciphertext": f"vault:v1:{plaintext}"}}
            )
        return httpx.Response(404)

    kms = build_byok_kms(settings, transport=httpx.MockTransport(handler))
    assert isinstance(kms, VaultByokKms)
    assert kms.key_id == "myk"
    assert _envelope(await kms.encrypt("sk-2"))["kms"] == "vault"
    assert [call.url.path for call in calls] == ["/v1/kv/encrypt/myk"]
    await kms.aclose()


def test_build_missing_config_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="BYOK_SECRET"):
        build_byok_kms(_settings(tmp_path, byok_secret=""))

    with pytest.raises(RuntimeError, match="BYOK_VAULT"):
        build_byok_kms(_settings(tmp_path, byok_kms="vault"))

    with pytest.raises(RuntimeError, match="未知的 BYOK KMS"):
        build_byok_kms(_settings(tmp_path, byok_kms="aws-kms"))


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.byok_kms == "local"
    assert settings.byok_kek_id == "local-1"
    assert settings.byok_previous_secrets == {}
    assert settings.byok_vault_mount == "transit"
    assert settings.byok_vault_key == "byok"
    assert settings.byok_vault_timeout_seconds == 5.0
