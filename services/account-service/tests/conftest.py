"""测试装配:每用例独立 sqlite 库;鉴权开,TestIdP 真签 token(架构 §10)。

账号即隔离边界:token 的 account claim 就是 account_id(= users.id)。
库/客户端样板来自 common.service_testkit(见 AGENTS.md「测试编排」)。
"""

from __future__ import annotations

from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app

_IDP = TestIdP()
# 以 account_id 直接签 token:不关心账号行的用例无需先注册
ACCOUNT_ID = "a" * 32
OTHER_ACCOUNT_ID = "b" * 32
ACCOUNT = _IDP.bearer(ACCOUNT_ID)
OTHER_ACCOUNT = _IDP.bearer(OTHER_ACCOUNT_ID)


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


_settings = settings_factory(Settings)
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)


def _register_account(
    client: TestClient, email: str = "alice@example.com", password: str = "hunter2ok"
) -> dict:
    """注册一个账号,返回 TokenResponse(account_id = users.id)。"""
    response = client.post(
        "/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()
