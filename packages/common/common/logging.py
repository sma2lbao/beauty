"""结构化日志(structlog)。

- dev:ConsoleRenderer(人读友好);staging/prod:JSON(机器采集);
- service/env 通过 contextvars 绑定,服务内所有日志自动带身份;
- 请求级字段(request_id/tenant_id/user_id…)由 `common.middleware` 在
  请求边界绑定,业务代码只读不写(见 docs/observability.md §5)。
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from common.config import BaseAppSettings

# 进程级固定字段:清空请求上下文后要重新绑回,否则服务身份丢失。
_BASE_CONTEXT: dict[str, Any] = {}


def setup_logging(settings: BaseAppSettings) -> None:
    """按服务配置初始化结构化日志(服务 lifespan 中调用一次)。"""
    level = logging.getLevelName(settings.log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    use_json = (
        settings.env in ("staging", "prod")
        if settings.log_json is None
        else settings.log_json
    )
    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if use_json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _BASE_CONTEXT.clear()
    _BASE_CONTEXT.update(service=settings.service_name, env=settings.env)
    structlog.contextvars.bind_contextvars(**_BASE_CONTEXT)


def bind_request_context(**fields: Any) -> None:
    """绑定请求级字段(request_id/tenant_id/error_code…),随日志自动输出。"""
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """清空请求级字段,并恢复进程级身份(必须在 finally 中调用)。"""
    structlog.contextvars.clear_contextvars()
    if _BASE_CONTEXT:
        structlog.contextvars.bind_contextvars(**_BASE_CONTEXT)


def current_context() -> dict[str, Any]:
    """读取当前 contextvars(用于出站透传与调试,不要用于拼接日志)。"""
    return structlog.contextvars.get_contextvars()


def redact(value: str | None, *, keep_head: int = 4, keep_tail: int = 2) -> str:
    """脱敏凭证:只保留首尾少量字符,过短的串直接打码。"""
    if not value:
        return ""
    if len(value) <= keep_head + keep_tail + 4:
        return "***"
    return f"{value[:keep_head]}…{value[-keep_tail:]}"


def get_logger(name: str | None = None):
    """获取结构化 logger(setup_logging 之后使用)。"""
    return structlog.get_logger(name)
