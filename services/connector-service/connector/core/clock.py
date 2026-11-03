"""时间工具:全服务用 UTC;sqlite 读回的 DateTime 是 naive,统一按 UTC 解释。"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """naive datetime 视为 UTC(sqlite 不保留时区);aware 原样返回。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_expiring(expires_at: datetime | None, margin_seconds: int) -> bool:
    """空值视为未知(不触发刷新);剩余寿命 <= 余量即视为将过期。"""
    if expires_at is None:
        return False
    remaining = (as_utc(expires_at) - utcnow()).total_seconds()
    return remaining <= margin_seconds
