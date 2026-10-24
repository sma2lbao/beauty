"""服务脚手架(service-kit):统一各服务重复的应用装配、健康探针、配置基类与生命周期。

R-22 试点:connector-service / knowledge-service 已迁入;其余服务 W3 跟进。
"""

from __future__ import annotations

from common.service_kit.app import RouterBinding, create_service_app
from common.service_kit.health import ReadyCheck, create_health_router
from common.service_kit.internal_auth import auth_dependencies
from common.service_kit.lifespan import (
    LifespanFactory,
    ShutdownHook,
    StartupHook,
    build_lifespan,
    install_database,
)
from common.service_kit.settings import ServiceSettings

__all__ = [
    "LifespanFactory",
    "ReadyCheck",
    "RouterBinding",
    "ServiceSettings",
    "ShutdownHook",
    "StartupHook",
    "auth_dependencies",
    "build_lifespan",
    "create_health_router",
    "create_service_app",
    "install_database",
]
