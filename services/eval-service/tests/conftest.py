"""测试装配:每用例独立 sqlite 库 + 真 token(零网络)。"""

from __future__ import annotations

from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP

from eval_service.core.config import Settings
from eval_service.main import create_app

_IDP = TestIdP()
AUTH = _IDP.bearer("t-1")
INTERNAL = {"X-Internal-Secret": "test-internal-secret"}

_settings = settings_factory(
    Settings,
    defaults={"internal_secret": "test-internal-secret"},
)

client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)

# 只读端点专用:模拟 AUTH_ENABLED=false(没有 verifier),验证匿名读取仍可用
_anon_settings = settings_factory(
    Settings,
    defaults={"internal_secret": "test-internal-secret", "auth_enabled": False},
)
anon_client = client_fixture(create_app, _anon_settings, name="anon_client")
