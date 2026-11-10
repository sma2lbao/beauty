"""管理面业务(admin-plane §3):账号检索/详情、配额/角色/状态与审计查询。

- 本模块是**唯一允许跨账号读**的账号面业务(管理面自身),其余路径仍由
  仓储的账号隔离保证;跨账号聚合只用于管理端列表/详情的用量展示;
- 所有写动作与审计同事务:调用方(API 层 session_scope)统一提交;
- 数据访问一律经仓储,仓储不提交(见 AGENTS.md「数据访问走仓储」)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from common.errors import ConflictError
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import AuditLog, User
from account.repositories import (
    AdminUsageRepository,
    AuditLogRepository,
    RefreshTokenRepository,
    SessionRepository,
    UserRepository,
)
from account.schemas.admin import (
    AdminAccount,
    AdminAccountDetail,
    AdminAccountList,
    AdminUsage,
    AuditLogList,
    AuditLogRead,
    CostAccountRow,
    CostDayRow,
    CostModelRow,
    CostSourceRow,
    CostSummary,
    CostTotals,
    QuotaUpdate,
    RoleUpdate,
    StatusUpdate,
)
from account.schemas.insights import UsageSummary
from account.services import insights as insights_service
from account.services.audit import AuditContext, record


def _start_of_month() -> datetime:
    """自然月 UTC 起点:与 insights 的 /usage/summary 同口径(admin-plane §3.2)。

    本模块刻意不复用 insights 的私有函数:聚合口径改动时两边会同时被测试盯住。
    """
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _admin_account(account: User, usage: AdminUsage) -> AdminAccount:
    """ORM 账号 + 聚合用量 → 管理面视图。"""
    return AdminAccount(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        status=account.status,
        role=account.role,
        quota=account.quota or {},
        created_at=account.created_at,
        usage=usage,
    )


def _usage_from_summary(usage: UsageSummary) -> AdminUsage:
    return AdminUsage(
        month_tokens=usage.month_tokens,
        month_events=usage.month_events,
        month_cost_usd=usage.month_cost_usd,
        unpriced_events=usage.unpriced_events,
    )


async def _usage_map(
    session: AsyncSession, account_ids: Sequence[str]
) -> dict[str, AdminUsage]:
    """一页账号的本月用量(单条 group by 聚合,避免 N+1)。"""
    totals = await AdminUsageRepository(session).month_totals(
        account_ids, _start_of_month()
    )
    return {
        account_id: AdminUsage(
            month_tokens=tokens,
            month_events=events,
            month_cost_usd=cost,
            unpriced_events=unpriced,
        )
        for account_id, (tokens, events, cost, unpriced) in totals.items()
    }


async def _detail(session: AsyncSession, account: User) -> AdminAccount:
    """单个账号返回体:复用 /usage/summary 的聚合,保证与用量页一致。"""
    usage = await insights_service.usage_summary(session, account.id)
    return _admin_account(account, _usage_from_summary(usage))


async def list_accounts(
    session: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> AdminAccountList:
    """账号分页列表:q 对 email/display_name 大小写不敏感包含。"""
    criteria: list[Any] = []
    keyword = (q or "").strip()
    if keyword:
        pattern = f"%{keyword.lower()}%"
        criteria.append(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.display_name).like(pattern),
            )
        )
    if status:
        criteria.append(User.status == status)

    users = UserRepository(session)
    records = await users.search(*criteria, limit=limit, offset=offset)
    total = await users.count(*criteria)
    usages = await _usage_map(session, [record.id for record in records])
    return AdminAccountList(
        items=[
            _admin_account(record, usages.get(record.id, AdminUsage()))
            for record in records
        ],
        total=total,
    )


async def get_account_detail(
    session: AsyncSession, account_id: str
) -> AdminAccountDetail:
    """账号详情(404 由仓储 require 保证);读端点不审计。"""
    account = await UserRepository(session).require(account_id)
    usage = await insights_service.usage_summary(session, account.id)
    return AdminAccountDetail(
        account=_admin_account(account, _usage_from_summary(usage)), usage=usage
    )


async def update_quota(
    session: AsyncSession,
    *,
    actor: User,
    account_id: str,
    data: QuotaUpdate,
    ctx: AuditContext,
) -> AdminAccount:
    """改配额:省略 = 保持,显式 null = 清除该键(快照语义,不做深层合并)。"""
    users = UserRepository(session)
    account = await users.require(account_id)
    before = dict(account.quota or {})
    quota = dict(before)
    for field in ("monthly_tokens", "max_concurrent_runs"):
        if field not in data.model_fields_set:
            continue
        value = getattr(data, field)
        if value is None:
            quota.pop(field, None)
        else:
            quota[field] = value
    account.quota = quota
    account = await users.add(account)
    await record(
        session,
        ctx,
        action="account.quota.update",
        target_type="account",
        target_id=account.id,
        detail={"before": before, "after": dict(account.quota)},
    )
    return await _detail(session, account)


async def update_role(
    session: AsyncSession,
    *,
    actor: User,
    account_id: str,
    data: RoleUpdate,
    ctx: AuditContext,
) -> AdminAccount:
    """改角色:禁止改自己(409),防止管理员互相/自身锁死入口。"""
    users = UserRepository(session)
    account = await users.require(account_id)
    if account.id == actor.id:
        raise ConflictError(
            "admin.self_role",
            "不能修改自己的角色",
            details={"account_id": account.id},
        )
    before = account.role
    account.role = data.role
    account = await users.add(account)
    await record(
        session,
        ctx,
        action="account.role.update",
        target_type="account",
        target_id=account.id,
        detail={"before": before, "after": account.role},
    )
    return await _detail(session, account)


async def update_status(
    session: AsyncSession,
    *,
    actor: User,
    account_id: str,
    data: StatusUpdate,
    ctx: AuditContext,
) -> AdminAccount:
    """改状态:停用同事务吊销该账号全部 refresh token 与 sessions。

    停用自己返回 409 admin.self_disable;access token 不设黑名单,
    停用后最多一个 token TTL(默认 1h)内仍能通过验签——但管理端每请求
    读库,require_admin 会立刻按 status 拒绝。
    """
    users = UserRepository(session)
    account = await users.require(account_id)
    if account.id == actor.id and data.status == "disabled":
        raise ConflictError(
            "admin.self_disable",
            "不能停用自己的账号",
            details={"account_id": account.id},
        )
    before = account.status
    account.status = data.status
    account = await users.add(account)

    revoked_refresh_tokens = 0
    revoked_sessions = 0
    if data.status == "disabled":
        now = datetime.now(UTC)
        revoked_refresh_tokens = await RefreshTokenRepository(
            session
        ).revoke_all_for_account(account.id, revoked_at=now)
        revoked_sessions = await SessionRepository(session).revoke_all_for_account(
            account.id
        )
    await record(
        session,
        ctx,
        action="account.status.update",
        target_type="account",
        target_id=account.id,
        detail={
            "before": before,
            "after": account.status,
            "revoked_refresh_tokens": revoked_refresh_tokens,
            "revoked_sessions": revoked_sessions,
        },
    )
    return await _detail(session, account)


async def list_audit_logs(
    session: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    actor_account_id: str | None = None,
    action: str | None = None,
    target_id: str | None = None,
) -> AuditLogList:
    """审计日志分页:created_at desc, id desc(同秒写入也能稳定排序)。"""
    criteria: list[Any] = []
    if actor_account_id:
        criteria.append(AuditLog.actor_account_id == actor_account_id)
    if action:
        criteria.append(AuditLog.action == action)
    if target_id:
        criteria.append(AuditLog.target_id == target_id)

    repo = AuditLogRepository(session)
    records = await repo.list_page(*criteria, limit=limit, offset=offset)
    total = await repo.count(*criteria)
    return AuditLogList(
        items=[AuditLogRead.model_validate(record) for record in records],
        total=total,
    )


#: 成本看板的 topN 与固定来源行(eval-and-cost-dashboard §3.2)
_TOP_MODELS = 20
_TOP_ACCOUNTS = 10
_COST_SOURCES = ("platform", "byok")


def _start_of_today() -> datetime:
    """UTC 今天 00:00:成本看板窗口起点按 UTC 自然日算。"""
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


async def cost_summary(session: AsyncSession, *, days: int = 30) -> CostSummary:
    """平台成本汇总(eval-and-cost-dashboard §3.2):管理面跨账号聚合。

    - 时间口径 UTC 自然日:窗口 = 含今天在内最近 days 天;
    - SQL 只做 GROUP BY,按天补零与 topN(模型 20、账号 10)在服务层做;
    - 未计价事件按 0 计入金额、单独计数(与 /usage/summary 同口径);
    - 读端点不写 audit_logs。
    """
    since = _start_of_today() - timedelta(days=days - 1)
    repo = AdminUsageRepository(session)

    (
        events,
        tokens,
        cost,
        unpriced,
        platform_events,
        byok_events,
    ) = await repo.cost_totals(since)

    source_totals = {
        source: (count, amount)
        for source, count, amount in await repo.cost_by_source(since)
    }
    cost_by_source = [
        CostSourceRow(
            source=source,
            events=source_totals.get(source, (0, 0))[0],
            cost_usd=float(source_totals.get(source, (0, 0))[1]),
        )
        for source in _COST_SOURCES
    ]

    day_totals = {
        date: (day_events, day_tokens, day_cost, day_unpriced)
        for date, day_events, day_tokens, day_cost, day_unpriced in (
            await repo.cost_by_day(since)
        )
    }
    window_dates = [
        (since + timedelta(days=offset)).date().isoformat() for offset in range(days)
    ]
    by_day = []
    for date in window_dates:
        day_events, day_tokens, day_cost, day_unpriced = day_totals.get(
            date, (0, 0, 0, 0)
        )
        by_day.append(
            CostDayRow(
                date=date,
                events=day_events,
                tokens=day_tokens,
                cost_usd=float(day_cost),
                unpriced_events=day_unpriced,
            )
        )

    ranked_models = sorted(
        await repo.cost_by_model(since),
        key=lambda row: (-row[3], -row[1], row[0]),
    )[:_TOP_MODELS]
    by_model = [
        CostModelRow(
            model=model,
            events=model_events,
            tokens=model_tokens,
            cost_usd=float(model_cost),
            unpriced_events=model_unpriced,
        )
        for model, model_events, model_tokens, model_cost, model_unpriced in (
            ranked_models
        )
    ]

    ranked_accounts = sorted(
        await repo.cost_by_account(since),
        key=lambda row: (-row[3], -row[1], row[0]),
    )[:_TOP_ACCOUNTS]
    emails = await UserRepository(session).emails_for(
        [row[0] for row in ranked_accounts]
    )
    top_accounts = [
        CostAccountRow(
            account_id=account_id,
            email=emails.get(account_id),
            events=account_events,
            tokens=account_tokens,
            cost_usd=float(account_cost),
        )
        for account_id, account_events, account_tokens, account_cost in ranked_accounts
    ]

    return CostSummary(
        days=days,
        since=since,
        totals=CostTotals(
            events=events,
            tokens=tokens,
            cost_usd=float(cost),
            unpriced_events=unpriced,
            platform_events=platform_events,
            byok_events=byok_events,
        ),
        cost_by_source=cost_by_source,
        by_day=by_day,
        by_model=by_model,
        top_accounts=top_accounts,
    )
