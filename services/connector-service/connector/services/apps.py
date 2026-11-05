"""账号级应用凭证解析:account_apps 行优先,缺失/不可用时回退全局 Settings。

解析顺序(设计 §5.2):账号行 -> 全局 Settings。账号行只有 app_id 与 app_secret
都可用时才生效——半配置的行说明管理员还没填完,回退全局比让 provider 变成
「未配置」更安全,同时记一条 warning 便于排查。

存储约定与连接凭证同一套信封加密(见 connector.core.crypto):

- 写入:account_apps.app_secret_encrypted = encrypt_app_secret(settings, "<明文>"),
  即 encrypt_credentials(credential_secret, {"app_secret": "<明文>"});
- 读取:decrypt_credentials 解出 {"app_secret": ...}(兼容历史明文行)。

app_secret 只在解析后的 provider 实例内存里存在;这里不写日志、不返回明文。
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.config import Settings
from connector.core.crypto import decrypt_credentials, encrypt_credentials
from connector.providers import require_provider
from connector.providers.base import BaseProvider
from connector.repositories import AccountAppRepository

#: app_secret 在加密 payload 里的键名(与文档约定一致,变更需迁移历史数据)
SECRET_PAYLOAD_KEY = "app_secret"

_log = get_logger("connector.apps")


def encrypt_app_secret(settings: Settings, app_secret: str) -> dict[str, Any]:
    """把账号 app_secret 包成可直接落库的加密信封。"""
    return encrypt_credentials(
        settings.credential_secret, {SECRET_PAYLOAD_KEY: app_secret}
    )


def decrypt_app_secret(settings: Settings, stored: Any) -> str | None:
    """解出 app_secret;不可解密或格式不对时返回 None(调用方回退全局)。"""
    if not isinstance(stored, dict) or not stored:
        return None
    try:
        payload = decrypt_credentials(settings.credential_secret, stored)
    except Exception as exc:  # 密钥轮换/数据损坏:不因单行账号配置中断请求
        _log.warning(
            "account_app_secret_unreadable", error=f"{type(exc).__name__}: {exc}"
        )
        return None
    secret = payload.get(SECRET_PAYLOAD_KEY)
    return str(secret) if secret else None


async def provider_for_account(
    session: AsyncSession,
    settings: Settings,
    account_id: str,
    provider_name: str,
) -> BaseProvider:
    """解析某账号该 provider 的实例:账号应用凭证优先,否则用全局配置。

    返回的 provider 已带上正确的 app_id/app_secret,调用方(授权、刷新、执行)
    直接用它即可;未知 provider 仍按 404 处理(与 require_provider 一致)。
    """
    provider = require_provider(settings, provider_name)
    row = await AccountAppRepository(session).get_for_provider(
        account_id, provider.name
    )
    if row is None:
        return provider
    secret = decrypt_app_secret(settings, row.app_secret_encrypted)
    if not row.app_id or not secret:
        _log.warning(
            "account_app_incomplete",
            account_id=account_id,
            provider=provider.name,
            has_app_id=bool(row.app_id),
            has_secret=bool(secret),
        )
        return provider
    return provider.with_account_app(row.app_id, secret)
