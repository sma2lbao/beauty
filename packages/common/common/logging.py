"""结构化日志(structlog)。

- dev:ConsoleRenderer(人读友好);staging/prod:JSON(机器采集);
- service/env 通过 contextvars 绑定,服务内所有日志自动带身份。
"""

from __future__ import annotations

import logging

import structlog

from common.config import BaseAppSettings


def setup_logging(settings: BaseAppSettings) -> None:
    """按服务配置初始化结构化日志(服务 lifespan 中调用一次)。"""
    level = logging.getLevelName(settings.log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO

    use_json = settings.env in ("staging", "prod")
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
    structlog.contextvars.bind_contextvars(
        service=settings.service_name, env=settings.env
    )


def get_logger(name: str | None = None):
    """获取结构化 logger(setup_logging 之后使用)。"""
    return structlog.get_logger(name)
