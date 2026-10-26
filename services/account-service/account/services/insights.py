"""用量聚合与 BYOK 存取(信封加密 v2,KMS 见 account.core.byok_kms)。"""

from __future__ import annotations

from datetime import UTC, datetime

from common.errors import AppError, NotFoundError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.byok_kms import BYOKKms
from account.models.account import BYOKKey, UsageEvent
from account.repositories import (
    BYOKRepository,
    OrganizationRepository,
    UsageEventRepository,
)
from account.schemas.insights import (
    BYOKRead,
    BYOKRotateResult,
    BYOKSecretRead,
    BYOKUpsert,
    UsageByModel,
    UsageEventCreate,
    UsageQuota,
    UsageSummary,
)

log = get_logger("account.insights")


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


def _positive_int(value: object) -> int | None:
    """quota JSON 里的数字:非正数/非数字一律当未配置(不限制)。"""
    if isinstance(value, bool):  # bool 是 int 子类,显式挡掉
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value > 0:
        return int(value)
    return None


async def usage_quota(session: AsyncSession, tenant_id: str) -> UsageQuota:
    """租户配额快照:organizations.quota + 本月已用 token。

    quota JSON 结构(宽松,缺省即不限制):
    ``{"monthly_tokens": 500000, "max_concurrent_runs": 3}``。
    组织档案不存在时按未配置处理(不拦创建)。
    """
    organization = await OrganizationRepository(session).get(tenant_id)
    quota = (organization.quota if organization is not None else None) or {}
    events = await UsageEventRepository(session).list_since(
        tenant_id, _start_of_month()
    )
    return UsageQuota(
        monthly_tokens=_positive_int(quota.get("monthly_tokens")),
        max_concurrent_runs=_positive_int(quota.get("max_concurrent_runs")),
        used_tokens=sum(event.total_tokens for event in events),
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
    kms: BYOKKms,
) -> BYOKRead:
    normalized = provider.strip().lower()
    if not normalized or len(normalized) > 32:
        raise AppError("byok.invalid_provider", "provider 无效")
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(tenant_id, normalized)
    encrypted = await kms.encrypt(data.api_key)
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


async def get_byok_secret(
    session: AsyncSession,
    tenant_id: str,
    provider: str,
    *,
    kms: BYOKKms,
) -> BYOKSecretRead:
    """内部解析明文 key(llm-gateway 按租户取用)。

    历史密文(kid 不是当前 kid / v2 之前的裸 Fernet token)在读取时懒轮换为
    当前信封;轮换失败只记 warning,仍返回明文(下次读再试,不阻断调用面)。
    """
    record = await BYOKRepository(session).find_for_provider(
        tenant_id, provider.strip().lower()
    )
    if record is None:
        raise NotFoundError("byok.not_found", "BYOK 密钥不存在")
    plaintext = await kms.decrypt(record.encrypted_key)
    if kms.needs_rotation(record.encrypted_key):
        try:
            record.encrypted_key = await kms.rotate(record.encrypted_key)
        except AppError as exc:
            log.warning(
                "byok.rotate_failed",
                tenant_id=tenant_id,
                provider=record.provider,
                key_id=kms.key_id,
                code=exc.code,
            )
    return BYOKSecretRead(
        provider=record.provider,
        api_key=plaintext,
        key_hint=record.key_hint,
        key_id=kms.key_id,
    )


async def rotate_byok(
    session: AsyncSession, tenant_id: str, *, kms: BYOKKms
) -> BYOKRotateResult:
    """把租户全部 BYOK 密文重加密到当前 KMS key;逐条容错、不抛。"""
    records = await BYOKRepository(session).list_scoped(
        tenant_id, order_by=[BYOKKey.provider]
    )
    rotated = 0
    failed = 0
    for record in records:
        try:
            record.encrypted_key = await kms.rotate(record.encrypted_key)
            rotated += 1
        except AppError as exc:
            failed += 1
            log.warning(
                "byok.rotate_failed",
                tenant_id=tenant_id,
                provider=record.provider,
                key_id=kms.key_id,
                code=exc.code,
            )
    log.info("byok.rotated", tenant_id=tenant_id, key_id=kms.key_id, rotated=rotated)
    return BYOKRotateResult(rotated=rotated, failed=failed)


async def delete_byok(session: AsyncSession, tenant_id: str, provider: str) -> None:
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(tenant_id, provider.strip().lower())
    if record is None:
        raise AppError("byok.not_found", "BYOK 密钥不存在")
    await repo.delete(record)
