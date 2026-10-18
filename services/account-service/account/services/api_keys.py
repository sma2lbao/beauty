"""API Key 业务(M3-01):生成/轮换/吊销/校验;只存哈希。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from common.errors import AppError, UnauthorizedError
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import ApiKey
from account.repositories import ApiKeyRepository
from account.schemas.api_keys import (
    ALLOWED_SCOPES,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ApiKeyVerifyRead,
)

_KEY_PREFIX = "bm_live_"


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_key() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def _to_read(record: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        key_hint=record.key_hint,
        masked_key=f"{record.prefix}…{record.key_hint}",
        scopes=list(record.scopes or []),
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


def _validate_scopes(scopes: list[str]) -> list[str]:
    invalid = [scope for scope in scopes if scope not in ALLOWED_SCOPES]
    if invalid:
        raise AppError(
            "api_key.invalid_scope",
            f"不支持的 scope:{', '.join(invalid)}",
            status=422,
        )
    if not scopes:
        raise AppError("api_key.scope_required", "至少需要一个 scope", status=422)
    return scopes


async def list_keys(session: AsyncSession, tenant_id: str) -> list[ApiKeyRead]:
    records = await ApiKeyRepository(session).list_scoped(
        tenant_id, order_by=[ApiKey.created_at.desc()]
    )
    return [_to_read(record) for record in records]


async def create_key(
    session: AsyncSession, tenant_id: str, data: ApiKeyCreate
) -> ApiKeyCreated:
    scopes = _validate_scopes(data.scopes)
    plaintext = _new_key()
    record = ApiKey(
        tenant_id=tenant_id,
        name=data.name,
        prefix=plaintext[:16],
        key_hash=_hash_key(plaintext),
        key_hint=plaintext[-4:],
        scopes=scopes,
    )
    await ApiKeyRepository(session).add(record)
    return ApiKeyCreated(**_to_read(record).model_dump(), key=plaintext)


async def rotate_key(
    session: AsyncSession, tenant_id: str, key_id: str
) -> ApiKeyCreated:
    repo = ApiKeyRepository(session)
    record = await repo.require_for_tenant(tenant_id, key_id)
    if record.revoked_at is not None:
        raise AppError("api_key.revoked", "API Key 已吊销", status=409)
    record.revoked_at = datetime.now(UTC)
    created = await create_key(
        session,
        tenant_id,
        ApiKeyCreate(name=record.name, scopes=list(record.scopes or [])),
    )
    return created


async def revoke_key(
    session: AsyncSession, tenant_id: str, key_id: str
) -> None:
    repo = ApiKeyRepository(session)
    record = await repo.require_for_tenant(tenant_id, key_id)
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)


async def verify_key(session: AsyncSession, plaintext: str) -> ApiKeyVerifyRead:
    record = await ApiKeyRepository(session).find_by_hash(_hash_key(plaintext))
    if record is None or record.revoked_at is not None:
        raise UnauthorizedError("api_key.invalid", "API Key 无效或已吊销")
    record.last_used_at = datetime.now(UTC)
    return ApiKeyVerifyRead(
        tenant_id=record.tenant_id,
        api_key_id=record.id,
        scopes=list(record.scopes or []),
    )
