"""用量聚合与 BYOK 存取(信封加密 v2,KMS 见 account.core.byok_kms)。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from common.errors import AppError, NotFoundError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.byok_kms import BYOKKms
from account.models.account import BYOKKey, UsageEvent
from account.repositories import BYOKRepository, UsageEventRepository, UserRepository
from account.schemas.insights import (
    BYOKCatalogRead,
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

# provider:小写字母数字开头,后可含 . _ -;总长 1~32
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
# 模型 id:形如 "<provider>/<model>",只允许字母数字与 . _ : / + -
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$")
_MAX_MODELS = 32
_MAX_MODEL_LENGTH = 128


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _start_of_today() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _start_of_month() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


async def record_usage(
    session: AsyncSession, account_id: str, data: UsageEventCreate
) -> UsageEvent:
    total = data.prompt_tokens + data.completion_tokens
    event = UsageEvent(
        account_id=account_id,
        run_id=data.run_id,
        model=data.model or "unknown",
        prompt_tokens=data.prompt_tokens,
        completion_tokens=data.completion_tokens,
        total_tokens=total,
        cost_usd=data.cost_usd,
        billing_source=data.billing_source,
    )
    return await UsageEventRepository(session).add(event)


def _cost(event: UsageEvent) -> Decimal | None:
    """事件成本(Decimal);None = 未计价,聚合时按 0 计但单独计数。"""
    return Decimal(event.cost_usd) if event.cost_usd is not None else None


async def usage_summary(session: AsyncSession, account_id: str) -> UsageSummary:
    events = await UsageEventRepository(session).list_since(
        account_id, _start_of_month()
    )
    today = _start_of_today()
    today_events = [event for event in events if _aware(event.created_at) >= today]
    # model -> (tokens, events, cost_usd, unpriced_events)
    by_model: dict[str, tuple[int, int, Decimal, int]] = {}
    for event in events:
        tokens, count, cost, unpriced = by_model.get(
            event.model, (0, 0, Decimal("0"), 0)
        )
        event_cost = _cost(event)
        by_model[event.model] = (
            tokens + event.total_tokens,
            count + 1,
            cost + (event_cost or Decimal("0")),
            unpriced + (1 if event_cost is None else 0),
        )
    month_cost = sum((cost for _, _, cost, _ in by_model.values()), Decimal("0"))
    today_cost = sum(
        ((_cost(event) or Decimal("0")) for event in today_events), Decimal("0")
    )
    return UsageSummary(
        today_tokens=sum(event.total_tokens for event in today_events),
        month_tokens=sum(event.total_tokens for event in events),
        today_events=len(today_events),
        month_events=len(events),
        today_cost_usd=float(today_cost),
        month_cost_usd=float(month_cost),
        unpriced_events=sum(unpriced for _, _, _, unpriced in by_model.values()),
        by_model=[
            UsageByModel(
                model=model,
                tokens=tokens,
                events=count,
                cost_usd=float(cost),
                unpriced_events=unpriced,
            )
            for model, (tokens, count, cost, unpriced) in sorted(
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


async def usage_quota(session: AsyncSession, account_id: str) -> UsageQuota:
    """账号配额快照:users.quota + 本月已用 token。

    quota JSON 结构(宽松,缺省即不限制,fail-open):
    ``{"monthly_tokens": 500000, "max_concurrent_runs": 3}``。
    新账号 quota 为空字典 → 两个维度均为 None(不限);账号缺失也按未配置处理。
    """
    account = await UserRepository(session).get(account_id)
    quota = (account.quota if account is not None else None) or {}
    events = await UsageEventRepository(session).list_since(
        account_id, _start_of_month()
    )
    return UsageQuota(
        monthly_tokens=_positive_int(quota.get("monthly_tokens")),
        max_concurrent_runs=_positive_int(quota.get("max_concurrent_runs")),
        used_tokens=sum(event.total_tokens for event in events),
    )


def _normalize_provider(provider: str) -> str:
    """provider 归一:strip().lower();不匹配字符集/长度即 400 byok.invalid_provider。"""
    normalized = provider.strip().lower()
    if _PROVIDER_PATTERN.fullmatch(normalized) is None:
        raise AppError("byok.invalid_provider", "provider 无效")
    return normalized


def _normalize_models(provider: str, models: list[str]) -> list[str]:
    """模型清单归一:strip、丢空、去重保序,并校验前缀/字符集/长度/条数。

    模型必须属于本 provider("<provider>/" 前缀),这样网关按前缀路由时
    不会出现「A provider 的 key 调 B provider 的模型」。
    """
    prefix = f"{provider}/"
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in models:
        value = raw.strip()
        if not value:
            continue
        if (
            len(value) > _MAX_MODEL_LENGTH
            or not value.startswith(prefix)
            or _MODEL_PATTERN.fullmatch(value) is None
        ):
            raise AppError("byok.invalid_model", "model 无效")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if len(normalized) > _MAX_MODELS:
        raise AppError("byok.invalid_model", "model 数量超过 32 条上限")
    return normalized


def _to_read(record: BYOKKey) -> BYOKRead:
    return BYOKRead(
        provider=record.provider,
        masked_key="••••" + record.key_hint,
        key_hint=record.key_hint,
        updated_at=record.updated_at,
        models=list(record.models or []),
    )


async def list_byok(session: AsyncSession, account_id: str) -> list[BYOKRead]:
    records = await BYOKRepository(session).list_scoped(
        account_id, order_by=[BYOKKey.provider]
    )
    return [_to_read(record) for record in records]


async def upsert_byok(
    session: AsyncSession,
    account_id: str,
    provider: str,
    data: BYOKUpsert,
    *,
    kms: BYOKKms,
) -> BYOKRead:
    normalized = _normalize_provider(provider)
    models = _normalize_models(normalized, data.models)
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(account_id, normalized)
    api_key = (data.api_key or "").strip()
    if api_key:
        encrypted = await kms.encrypt(api_key)
        hint = api_key[-4:]
    elif record is None:
        # 首次保存必须有 key,否则没有任何可保留的密文
        raise AppError("byok.invalid_key", "首次保存 BYOK 必须提供 api_key")
    else:
        # 只改模型:复用旧密文与提示,不重新加密(避免无谓的 KMS 调用)
        encrypted = record.encrypted_key
        hint = record.key_hint
    if record is None:
        record = BYOKKey(
            account_id=account_id,
            provider=normalized,
            encrypted_key=encrypted,
            key_hint=hint,
            models=models,
        )
        await repo.add(record)
    else:
        record.encrypted_key = encrypted
        record.key_hint = hint
        record.models = models
    return _to_read(record)


async def custom_models(session: AsyncSession, account_id: str) -> BYOKCatalogRead:
    """账号 BYOK 自定义模型并集:按 provider 排序展开,全局去重保序。

    内部端点(llm-gateway)用它把「账号自定义模型」并入模型目录与白名单;
    只读,跨账号隔离由仓储保证。
    """
    records = await BYOKRepository(session).list_scoped(
        account_id, order_by=[BYOKKey.provider]
    )
    seen: set[str] = set()
    models: list[str] = []
    for record in records:
        for model in record.models or []:
            if model in seen:
                continue
            seen.add(model)
            models.append(model)
    return BYOKCatalogRead(models=models)


async def get_byok_secret(
    session: AsyncSession,
    account_id: str,
    provider: str,
    *,
    kms: BYOKKms,
) -> BYOKSecretRead:
    """内部解析明文 key(llm-gateway 按账号取用)。

    历史密文(kid 不是当前 kid / v2 之前的裸 Fernet token)在读取时懒轮换为
    当前信封;轮换失败只记 warning,仍返回明文(下次读再试,不阻断调用面)。
    """
    record = await BYOKRepository(session).find_for_provider(
        account_id, provider.strip().lower()
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
                account_id=account_id,
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
    session: AsyncSession, account_id: str, *, kms: BYOKKms
) -> BYOKRotateResult:
    """把账号全部 BYOK 密文重加密到当前 KMS key;逐条容错、不抛。"""
    records = await BYOKRepository(session).list_scoped(
        account_id, order_by=[BYOKKey.provider]
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
                account_id=account_id,
                provider=record.provider,
                key_id=kms.key_id,
                code=exc.code,
            )
    log.info("byok.rotated", account_id=account_id, key_id=kms.key_id, rotated=rotated)
    return BYOKRotateResult(rotated=rotated, failed=failed)


async def delete_byok(session: AsyncSession, account_id: str, provider: str) -> None:
    repo = BYOKRepository(session)
    record = await repo.find_for_provider(account_id, provider.strip().lower())
    if record is None:
        raise AppError("byok.not_found", "BYOK 密钥不存在")
    await repo.delete(record)
