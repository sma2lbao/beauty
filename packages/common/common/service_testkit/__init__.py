"""测试基座(仅测试使用):零网络 Settings 构造 + TestClient 装配工厂。

各服务 tests/conftest.py 共有三段重复样板:

1. 每用例独立 sqlite 文件库 + auto_create_tables;
2. 自足配置两道防线:_env_file=None,并显式钉住会连外部的开关(见 AGENTS.md);
3. create_app(...) + TestClient 进入/退出 lifespan。

本模块把这三段收成 settings_factory / client_fixture / app_client。
pytest 只在测试上下文 import(本模块不会被服务的运行时代码引入),
因此 pytest 属于 common 的 dev 依赖组,不是服务运行时依赖。
"""

from common.service_testkit.client import app_client, client_fixture
from common.service_testkit.settings import (
    NETWORK_SWITCHES,
    build_settings,
    settings_factory,
    sqlite_url,
)

__all__ = [
    "NETWORK_SWITCHES",
    "app_client",
    "build_settings",
    "client_fixture",
    "settings_factory",
    "sqlite_url",
]
