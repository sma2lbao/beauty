"""beauty-moon 服务基础设施共享库。

当前内容:统一错误模型。后续按 docs/architecture.md 补齐:
配置基类(pydantic-settings)、结构化日志、FastAPI 公共依赖、观测装配、SSE 工具。
"""

from common.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError

__all__ = [
    "AppError",
    "ConflictError",
    "NotFoundError",
    "PermissionDeniedError",
]
