"""测试装配:每用例独立 sqlite 库;连接行直接落库(服务无创建端点);鉴权开 + 真 token。

库/客户端样板来自 common.service_testkit(见 AGENTS.md「测试编排」)。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP

from connector.core.config import Settings
from connector.core.crypto import encrypt_credentials
from connector.main import create_app

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")

# 与 Settings.credential_secret 默认值保持一致,便于测试解密直落库的凭证
CREDENTIAL_SECRET = "dev-connector-secret-change-me"

DEFAULT_CREDENTIALS = {
    "email": "me@gmail.com",
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_at": 0,
}

_settings = settings_factory(Settings)
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)


def insert_connection(
    db_path: Path,
    *,
    tenant_id: str = "t-1",
    provider: str = "gmail",
    account: str = "me@gmail.com",
    status: str = "active",
    credentials: dict | None = None,
) -> dict:
    """直接落一行连接:服务当前没有创建端点,用历史数据模拟读/撤销端点。"""
    connection_id = uuid.uuid4().hex
    payload = encrypt_credentials(
        CREDENTIAL_SECRET,
        DEFAULT_CREDENTIALS if credentials is None else credentials,
    )
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            "INSERT INTO connections "
            "(id, tenant_id, provider, account, display_name, status, credentials) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                connection_id,
                tenant_id,
                provider,
                account,
                None,
                status,
                json.dumps(payload),
            ),
        )
    return {
        "id": connection_id,
        "tenant_id": tenant_id,
        "provider": provider,
        "account": account,
        "status": status,
    }
