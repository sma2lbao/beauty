"""统一错误模型。

约定:

- 所有服务的业务错误继承 AppError,对外 API 由统一异常处理器渲染为
  ``{"code", "message", "details"}`` 结构;
- code 是对外契约的一部分,一经发布不再变更,形如 ``account.not_found``;
- status 是该类错误的默认 HTTP 状态建议,处理器可按上下文覆盖。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务错误基类。"""

    status: int = 400

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应片段。"""
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AppError):
    """资源不存在。"""

    status = 404


class PermissionDeniedError(AppError):
    """无权限(含操作策略 deny)。"""

    status = 403


class ConflictError(AppError):
    """状态冲突(如重复连接、乐观锁冲突)。"""

    status = 409


class UpstreamError(AppError):
    """外部依赖失败(模型/邮件/三方 API),幂等可重试。"""

    status = 502


class QuotaExceededError(AppError):
    """配额/预算用尽(用量、并发数、token 预算)——拒绝新任务但可稍后重试。"""

    status = 429


class UnauthorizedError(AppError):
    """未认证或凭证不可信(缺失/过期/签名不符/缺租户 claim)。"""

    status = 401


class StorageError(AppError):
    """存储故障(对象键非法、读写失败)——内部基础设施问题。"""

    status = 500
