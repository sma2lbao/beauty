"""测试装配:无 DB;模型调用一律 monkeypatch litellm,不发真实请求。

配置构造(setting=dev、_env_file 置空、外部开关钉死)来自
common.service_testkit(见 AGENTS.md「测试编排」)。

BYOK/内部鉴权也在这里钉死:默认不启用 BYOK(测试零网络,不向
account-service 换密钥),INTERNAL_SECRET 留空(本地 dev 放行语义)。
需要打开它们的用例自己 build_settings/注入 ByokResolver。
"""

from __future__ import annotations

from common.service_testkit import client_fixture, settings_factory

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app

_settings = settings_factory(
    Settings,
    defaults={"byok_enabled": False, "internal_secret": ""},
)
client = client_fixture(create_app, _settings)
