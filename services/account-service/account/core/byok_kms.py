"""BYOK 信封加密的 KMS 抽象(local / Vault Transit)。

密文信封 v2(JSON):``{"v":2,"kms":"local","kid":"local-1","ct":"<密文>"}``。

- `local`:Fernet + 本地密钥材料——`BYOK_SECRET` 是**当前 kid** 的密钥,
  `BYOK_PREVIOUS_SECRETS`(`{"旧kid": "旧密钥"}`)保存轮换期仍在用的历史密钥;
- `vault`:Vault Transit(hashivault)/v1/{mount}/encrypt|decrypt|rewrap/{key},
  密钥由 Vault 托管,信封里只放 Vault 密文与 key 名;
- **历史密文**是 v2 之前的裸 Fernet token(不以 `{` 开头):local 模式继续可解,
  读取/更新时懒重加密成 v2;Vault 模式无法解裸 token(需先 local 模式迁移)。

错误契约(与网关/前端解耦):解密失败 `StorageError("byok.unreadable")`,
KMS 不可用 `UpstreamError("byok.kms_unavailable")`——都不泄漏密文/密钥内容。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from common.errors import AppError, StorageError, UpstreamError
from common.security import SecretBox

from account.core.config import Settings

ENVELOPE_VERSION = 2
_UNREADABLE = ("byok.unreadable", "BYOK 密钥无法解密")
_UNAVAILABLE = ("byok.kms_unavailable", "BYOK KMS 不可用")


def _unreadable() -> StorageError:
    return StorageError(*_UNREADABLE)


def _unavailable() -> UpstreamError:
    return UpstreamError(*_UNAVAILABLE)


def _seal(kms_name: str, key_id: str, ciphertext: str) -> str:
    """把 KMS 密文包成 v2 信封(local/vault 共用)。"""
    return json.dumps(
        {"v": ENVELOPE_VERSION, "kms": kms_name, "kid": key_id, "ct": ciphertext},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _open_envelope(ciphertext: str) -> dict[str, Any] | None:
    """解析 v2 信封;历史裸 token 或非法信封返回 None。"""
    text = ciphertext.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("v") != ENVELOPE_VERSION:
        return None
    if not isinstance(payload.get("ct"), str) or not payload["ct"]:
        return None
    return payload


class BYOKKms(Protocol):
    """BYOK 加解密抽象:业务层只依赖本协议,local/vault 可替换。"""

    name: str
    key_id: str

    async def encrypt(self, plaintext: str) -> str:
        """加密为信封密文(v2 JSON)。"""
        ...

    async def decrypt(self, ciphertext: str) -> str:
        """解密信封或历史裸 token;失败抛 byok.unreadable。"""
        ...

    def needs_rotation(self, ciphertext: str) -> bool:
        """密文是否需要用当前 key 重加密(非信封或 kid 不是当前 kid)。"""
        ...

    async def rotate(self, ciphertext: str) -> str:
        """把密文重加密到当前 key,返回新信封。"""
        ...

    async def aclose(self) -> None:
        """释放底层资源(HTTP 客户端等);可重复调用。"""
        ...


class LocalByokKms:
    """Fernet 本地 KMS:当前密钥加密,历史 kid 密钥仍可解密。"""

    name = "local"

    def __init__(
        self,
        secret: str,
        *,
        key_id: str = "local-1",
        previous: Mapping[str, str] | None = None,
    ) -> None:
        self.key_id = key_id
        history = dict(previous or {})
        boxes = {kid: self._box(value) for kid, value in history.items()}
        # 当前 kid 永远指向当前密钥(key_id 与历史条目重名时以当前为准)
        boxes[key_id] = self._box(secret)
        self._boxes = boxes
        self._order = [key_id, *(kid for kid in history if kid != key_id)]

    @staticmethod
    def _box(secret: str) -> SecretBox:
        code, message = _UNREADABLE
        return SecretBox(secret, error_code=code, error_message=message)

    def _candidates(self, envelope: dict[str, Any] | None) -> list[SecretBox]:
        if envelope is None:
            # 历史裸 token:先试当前密钥,再按配置顺序试历史 kid
            return [self._boxes[kid] for kid in self._order]
        kid = envelope.get("kid")
        box = self._boxes.get(kid) if isinstance(kid, str) else None
        if box is None:
            raise _unreadable()
        return [box]

    async def encrypt(self, plaintext: str) -> str:
        ciphertext = self._boxes[self.key_id].encrypt(plaintext)
        return _seal(self.name, self.key_id, ciphertext)

    async def decrypt(self, ciphertext: str) -> str:
        envelope = _open_envelope(ciphertext)
        secret = envelope["ct"] if envelope is not None else ciphertext
        for box in self._candidates(envelope):
            try:
                return box.decrypt(secret)
            except AppError:
                continue
        raise _unreadable()

    def needs_rotation(self, ciphertext: str) -> bool:
        envelope = _open_envelope(ciphertext)
        return envelope is None or envelope.get("kid") != self.key_id

    async def rotate(self, ciphertext: str) -> str:
        return await self.encrypt(await self.decrypt(ciphertext))

    async def aclose(self) -> None:
        return None


class VaultByokKms:
    """Vault Transit KMS(兼容 /v1/{mount}/encrypt|decrypt|rewrap/{key})。"""

    name = "vault"

    def __init__(
        self,
        *,
        url: str,
        token: str,
        mount: str = "transit",
        key: str = "byok",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.key_id = key
        self._mount = mount
        self._token = token
        self._base_url = url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def _call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/v1/{self._mount}/{operation}/{self.key_id}"
        try:
            response = await self._client.post(
                url, json=payload, headers={"X-Vault-Token": self._token}
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _unavailable() from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise _unavailable()
        return data

    async def encrypt(self, plaintext: str) -> str:
        encoded = base64.b64encode(plaintext.encode()).decode()
        data = await self._call("encrypt", {"plaintext": encoded})
        ciphertext = data.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            raise _unavailable()
        return _seal(self.name, self.key_id, ciphertext)

    async def decrypt(self, ciphertext: str) -> str:
        envelope = _open_envelope(ciphertext)
        if envelope is None:
            # Vault 无法解 v2 之前的裸 Fernet token(需先 local 模式迁移)
            raise _unreadable()
        data = await self._call("decrypt", {"ciphertext": envelope["ct"]})
        encoded = data.get("plaintext")
        if not isinstance(encoded, str):
            raise _unavailable()
        try:
            return base64.b64decode(encoded).decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise _unavailable() from exc

    def needs_rotation(self, ciphertext: str) -> bool:
        envelope = _open_envelope(ciphertext)
        return envelope is None or envelope.get("kid") != self.key_id

    async def rotate(self, ciphertext: str) -> str:
        envelope = _open_envelope(ciphertext)
        if envelope is None:
            raise _unreadable()
        data = await self._call("rewrap", {"ciphertext": envelope["ct"]})
        rotated = data.get("ciphertext")
        if not isinstance(rotated, str) or not rotated:
            raise _unavailable()
        return _seal(self.name, self.key_id, rotated)

    async def aclose(self) -> None:
        await self._client.aclose()


def build_byok_kms(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> BYOKKms:
    """按配置装配 KMS;配置缺失/取值未知直接 RuntimeError(启动失败)。"""
    kind = (settings.byok_kms or "").strip().lower()
    if kind == "local":
        if not settings.byok_secret:
            raise RuntimeError("BYOK KMS 配置缺失: ACCOUNT__BYOK_SECRET")
        return LocalByokKms(
            settings.byok_secret,
            key_id=settings.byok_kek_id or "local-1",
            previous=settings.byok_previous_secrets,
        )
    if kind == "vault":
        if not settings.byok_vault_url or not settings.byok_vault_token:
            raise RuntimeError(
                "BYOK KMS 配置缺失: ACCOUNT__BYOK_VAULT_URL / ACCOUNT__BYOK_VAULT_TOKEN"
            )
        return VaultByokKms(
            url=settings.byok_vault_url,
            token=settings.byok_vault_token,
            mount=settings.byok_vault_mount or "transit",
            key=settings.byok_vault_key or "byok",
            timeout_seconds=settings.byok_vault_timeout_seconds or 5.0,
            transport=transport,
        )
    raise RuntimeError(f"未知的 BYOK KMS: {settings.byok_kms!r}")
