"""测试装配:每用例独立 sqlite 库;鉴权开,TestIdP 真签 token(架构 §10)。

库/客户端样板来自 common.service_testkit(见 AGENTS.md「测试编排」)。
"""

from __future__ import annotations

from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app

_IDP = TestIdP()
TENANT = _IDP.bearer("org-1")
OTHER = _IDP.bearer("org-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


_settings = settings_factory(Settings)
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)


def _create_user(
    client: TestClient, subject: str = "idp|u1", tenant: dict | None = None
):
    return client.post(
        "/v1/users",
        json={"subject": subject, "email": f"{subject}@example.com", "role": "owner"},
        headers=tenant or TENANT,
    )
