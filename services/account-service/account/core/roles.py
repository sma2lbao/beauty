"""账号角色:users.role 的取值与 ADMIN_EMAILS 名单匹配(admin-plane §2)。

- 角色**不进 JWT**:require_admin 每请求按 account_id 读库,降权/停用立即生效;
- ADMIN_EMAILS 是部署侧引导名单(env JSON 数组,大小写不敏感),命中即管理员;
  它只做"兜底放行 + 首次持久化",移出名单不会自动降权(降权走管理面显式改)。
"""

from __future__ import annotations

from account.core.config import Settings

ROLE_USER = "user"
ROLE_ADMIN = "admin"


def normalize_emails(emails: list[str]) -> set[str]:
    """名单归一:strip + lower,空白项丢弃(比较大小写不敏感)。"""
    return {email.strip().lower() for email in emails if email and email.strip()}


def is_admin_email(settings: Settings, email: str | None) -> bool:
    """邮箱是否命中 ADMIN_EMAILS(空邮箱恒不命中)。"""
    if not email:
        return False
    return email.strip().lower() in normalize_emails(settings.admin_emails)


def role_for_email(settings: Settings, email: str | None) -> str:
    """新账号的初始角色:命中名单即 admin,否则 user。"""
    return ROLE_ADMIN if is_admin_email(settings, email) else ROLE_USER
