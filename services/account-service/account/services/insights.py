"""用量聚合与 BYOK 存取(M0-11 本地版)。"""

from __future__ import annotations

from datetime import UTC, datetime

from common.errors import AppError
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.byok import encrypt_key
from account.models.account import BYOKKey, UsageEvent
from account.repositories import BYOKRepository, UsageEventRepository
from account.schemas.insights import (
    BYOKRead,
    BYOKUpsert,
    UsageByModel,
    UsageEventCreate,
    UsageSummary,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _start_of_today() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _start_of_month() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


async def record_usage(
    session: AsyncSession, tenant_id: str, data: UsageEventCreate
) -> UsageEvent:
    total = data.prompt_tokens + data.completion_tokens
    event = UsageEvent(
        tenant_id=tenant_id,
        run_id=data.run_id,
        model=data.model or "unknown",
        prompt_tokens=data.prompt_tokens,
        completion_tokens=data.completion_tokens,
        total_tokens=total,
    )
    return await UsageEventRepository(session).add(event)


async def usage_summary(session: AsyncSession, tenant_id: str) -> UsageSummary:
    events = await UsageEventRepository(session).list_since(
        tenant_id, _start_of_month()
    )
    today = _start_of_today()
    today_events = [event for event in events if _aware(event.created_at) >= today]
    by_model: dict[str, tuple[int, int]] = {}
    for event in events:
        tokens, count = by_model.get(event.model, (0, 0))
        by_model[event.model] = (tokens + event.total_tokens, count + 1)
    return UsageSummary(
        today_tokens=sum(event.total_tokens for event in today_events),
        month_tokens=sum(event.total_tokens for event in events),
        today_events=len(today_events),
        month_events=len(events),
        by_model=[
            UsageByModel(model=model, tokens=tokens, events=count)
            for model, (tokens, count) in sorted(
                by_model.items(), key=lambda item: item[1][0], reverse=True
            )
        ],
    )


def _to_read(record: BYOKKey) -> BYOKRead:
    return BYOKRead(
        provider=record.provider,
        masked_key="••••" + record.key_hint,
        key_hint=record.key_hint,
        updated_at=record.updated_at,
    )


async def list_byok(session: AsyncSession, tenant_id: str) -> list[BYOKRead]:
    records = await BYOKRepository(session).list_scoped(
        tenant_id, order_by=[BYOKKey.provider]
    )
    return [_to_read(record) for record in records]


async def upsert_byok(
    session: AsyncSession,
    tenant_id: str,
    provider: str,
    data: BYOKUpsert,
    *,
    secret: str,
) -> BYOKRead:
    normalized = provider.strip().lower()
    if not normalized or len(normalized) > 32:
        raise AppError("byok.invalid_provider", "provider 无效")
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(tenant_id, normalized)
    encrypted = encrypt_key(secret, data.api_key)
    hint = data.api_key[-4:]
    if record is None:
        record = BYOKKey(
            tenant_id=tenant_id,
            provider=normalized,
            encrypted_key=encrypted,
            key_hint=hint,
        )
        await repo.add(record)
    else:
        record.encrypted_key = encrypted
        record.key_hint = hint
    return _to_read(record)


async def delete_byok(
    session: AsyncSession, tenant_id: str, provider: str
) -> None:
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(tenant_id, provider.strip().lower())
    if record is None:
        raise AppError("byok.not_found", "BYOK 密钥不存在")
    await repo.delete(record)
