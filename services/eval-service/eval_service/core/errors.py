"""评测服务的专用错误类型。

HTTP 状态码由类属性决定(``AppError`` 本身不接受 status 参数),
这样处理器与调用方都能拿到稳定的语义状态。
"""

from __future__ import annotations

from common.errors import AppError, NotFoundError, StorageError, UpstreamError


class CaseNotFoundError(NotFoundError):
    """运行里不存在该用例。"""


class ReportMissingError(AppError):
    """报告对象已丢失或已过保留期。"""

    status = 410


class ReportStoreUnavailableError(AppError):
    """报告已转存对象存储，但当前实例没有装配存储。"""

    status = 503


class ReportStoreError(UpstreamError):
    """对象存储读取失败。"""


class ReportCorruptError(StorageError):
    """对象里的报告无法解析。"""
