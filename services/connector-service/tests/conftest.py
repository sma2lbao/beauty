"""测试装配:每用例独立 sqlite 库;示例上游全部 MockTransport 打桩(零网络)。

库/客户端样板来自 common.service_testkit(见 AGENTS.md「测试编排」)。
连接行仍直接落库,但授权回调会用真实端点建连接。

仓库内置 Notion provider(生产注册表里有它),但平台链路的测试一律用
tests.demo_provider 里的测试专用实现(provider key "demo"),在 import 本模块时注册进
_REGISTRY;Notion 自己的用例带独立 MockTransport(见 tests/test_notion_provider.py)。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP

from connector.core.config import Settings
from connector.core.crypto import encrypt_credentials
from connector.main import create_app
from connector.providers import _REGISTRY
from tests.demo_provider import DemoProvider

# 测试进程内注册唯一的 provider(生产代码不内置任何 provider)
_REGISTRY[DemoProvider.name] = DemoProvider

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")
INTERNAL_SECRET = "internal-test-secret"
INTERNAL_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET, "X-Tenant-Id": "t-1"}

# 与 Settings.credential_secret 默认值保持一致,便于测试解密直落库的凭证
CREDENTIAL_SECRET = "dev-connector-secret-change-me"

LEGACY_CREDENTIALS = {
    "email": "me@gmail.com",
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_at": 0,
}


def demo_credentials(
    *,
    access_token: str = "at-1",
    refresh_token: str | None = "rt-1",
    expires_in: int = 7200,
) -> dict[str, Any]:
    """新格式凭证 payload(与 providers.base.Credentials 对齐)。"""
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "scope": "offline_access docs:readonly",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
        "refresh_expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
    }


class DemoMock:
    """示例上游桩:按路径返回固定 JSON,可注入失败响应。"""

    def __init__(self) -> None:
        self.token_calls: list[dict[str, Any]] = []
        self.api_calls: list[tuple[str, str, str | None]] = []
        self.refresh_tokens = 0
        self.fail: dict[str, httpx.Response] = {}

    # ---- httpx 处理器 ----

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for marker, response in self.fail.items():
            if marker in path:
                return response
        if path.endswith("/oauth/token"):
            return self._token(request)
        self.api_calls.append(
            (request.method, str(request.url), request.headers.get("authorization"))
        )
        if path.endswith("/userinfo"):
            return _json(
                {
                    "code": 0,
                    "data": {
                        "account_id": "u-1",
                        "union_id": "un-1",
                        "name": "张三",
                        "workspace_id": "ws-1",
                        "avatar_url": "https://x/a.png",
                    },
                }
            )
        if path.endswith("/files"):
            return _json(
                {
                    "code": 0,
                    "data": {
                        "files": [
                            {
                                "token": "f1",
                                "name": "a.docx",
                                "type": "docx",
                                "url": "https://x/f1",
                                "parent_token": "",
                            }
                        ],
                        "has_more": False,
                        "next_page_token": "",
                    },
                }
            )
        if path.endswith("/raw_content"):
            return _json({"code": 0, "data": {"content": "hello"}})
        if path.endswith("/blocks"):
            return _json(
                {
                    "code": 0,
                    "data": {
                        "items": [{"block_id": "b1", "block_type": 2}],
                        "has_more": False,
                        "page_token": "",
                    },
                }
            )
        if path.endswith("/spaces"):
            return _json(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "space_id": "sp1",
                                "name": "知识库",
                                "description": "d",
                                "space_type": "team",
                                "visibility": "public",
                            }
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                }
            )
        if "/spaces/" in path and path.endswith("/nodes"):
            return _json(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "n1",
                                "obj_token": "d1",
                                "obj_type": "docx",
                                "parent_node_token": "",
                                "title": "节点",
                                "has_child": False,
                            }
                        ],
                        "has_more": False,
                        "page_token": "",
                    },
                }
            )
        return _json({"code": 0, "data": {}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.token_calls.append(body)
        if body.get("grant_type") == "refresh_token":
            self.refresh_tokens += 1
            suffix = self.refresh_tokens + 1
            return _json(
                {
                    "code": 0,
                    "access_token": f"at-{suffix}",
                    "refresh_token": f"rt-{suffix}",
                    "token_type": "Bearer",
                    "expires_in": 7200,
                    "refresh_token_expires_in": 31536000,
                    "scope": "offline_access docs:readonly",
                }
            )
        return _json(
            {
                "code": 0,
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "token_type": "Bearer",
                "expires_in": 7200,
                "refresh_token_expires_in": 31536000,
                "scope": "offline_access docs:readonly",
            }
        )


def _json(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.fixture(name="demo")
def demo_fixture() -> DemoMock:
    return DemoMock()


@pytest.fixture(name="demo_transport")
def demo_transport_fixture(demo: DemoMock) -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(demo.handler)


_settings = settings_factory(
    Settings,
    defaults={
        # 目录只暴露启用列表里的 provider;测试启用测试专用 demo
        "providers_enabled": "demo",
        "internal_secret": INTERNAL_SECRET,
        "oauth_redirect_base": "http://testserver",
        "oauth_success_redirect": "http://web.test/connectors",
        # 默认关闭外部副作用:不写 outbox(降级日志)、不起后台巡检任务
        "outbox_enabled": False,
        "refresh_patrol_enabled": False,
    },
)
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
    fixture_kwargs={"http_transport": "demo_transport"},
)


def _sqlite_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def insert_connection(
    db_path: Path,
    *,
    tenant_id: str = "t-1",
    provider: str = "demo",
    account: str = "u-1",
    status: str = "active",
    enabled: bool = True,
    credentials: dict | None = None,
    expires_at: datetime | None = None,
    owner_subject: str | None = "u1",
    last_refreshed_at: datetime | None = None,
) -> dict:
    """直接落一行连接(用于读/撤销/执行/巡检路径,不经过授权端点)。"""
    connection_id = uuid.uuid4().hex
    payload = encrypt_credentials(
        CREDENTIAL_SECRET,
        demo_credentials() if credentials is None else credentials,
    )
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            "INSERT INTO connections "
            "(id, tenant_id, provider, account, display_name, status, credentials, "
            " enabled, owner_subject, auth_kind, expires_at, scopes, metadata, "
            " last_refreshed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                connection_id,
                tenant_id,
                provider,
                account,
                "张三",
                status,
                json.dumps(payload),
                1 if enabled else 0,
                owner_subject,
                "user",
                _sqlite_dt(expires_at),
                json.dumps(["offline_access", "docs:readonly"]),
                json.dumps({"account_id": account}),
                _sqlite_dt(last_refreshed_at),
            ),
        )
    return {
        "id": connection_id,
        "tenant_id": tenant_id,
        "provider": provider,
        "account": account,
        "status": status,
    }


def insert_tenant_app(
    db_path: Path,
    *,
    tenant_id: str = "t-1",
    provider: str = "demo",
    app_id: str = "demo-tenant",
    app_secret: str = "tenant-secret",
    encrypted: bool = True,
) -> str:
    """直落一行租户应用凭证;默认按与其他凭证相同的信封加密存 secret。"""
    tenant_app_id = uuid.uuid4().hex
    if encrypted:
        stored = encrypt_credentials(CREDENTIAL_SECRET, {"app_secret": app_secret})
    else:
        stored = {"app_secret": app_secret}
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            "INSERT INTO tenant_apps "
            "(id, tenant_id, provider, app_id, app_secret_encrypted, config, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                tenant_app_id,
                tenant_id,
                provider,
                app_id,
                json.dumps(stored),
                json.dumps({}),
            ),
        )
    return tenant_app_id


def insert_oauth_session(
    db_path: Path,
    *,
    state: str = "state-1",
    tenant_id: str = "t-1",
    provider: str = "demo",
    status: str = "pending",
    expires_at: datetime | None = None,
) -> str:
    """直落一行授权会话,用于过期/重复消费等回调路径。"""
    session_id = uuid.uuid4().hex
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            "INSERT INTO oauth_sessions "
            "(id, tenant_id, provider, state_nonce, code_verifier, redirect_uri, "
            " scopes, status, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                session_id,
                tenant_id,
                provider,
                state,
                "verifier-1",
                "http://testserver/v1/oauth/demo/callback",
                json.dumps(["offline_access"]),
                status,
                _sqlite_dt(expires_at or datetime.now(UTC) + timedelta(minutes=5)),
            ),
        )
    return session_id
