"""keyset 分页游标:base64url(无填充)编码锚点 id,不暴露内部排序键。

锚点行由各仓储按 id 回查(keyset 用其 created_at/updated_at + id),所以游标
只需要装 id;非法游标统一给 400,错误码由调用方按域给(conversation./run.)。
"""

from __future__ import annotations

import base64

from common.errors import AppError


def encode_cursor(anchor_id: str) -> str:
    """把分页锚点编码成不透明游标(base64url,无填充)。"""
    raw = base64.urlsafe_b64encode(anchor_id.encode()).decode()
    return raw.rstrip("=")


def decode_cursor(cursor: str, *, error_code: str, message: str) -> str:
    """解码游标;任何解码失败都按调用方的错误码给 400(不泄漏内部格式)。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都视为坏游标
        raise AppError(error_code, message) from exc
