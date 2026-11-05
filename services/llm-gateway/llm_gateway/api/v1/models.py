"""模型列表(M0-11):静态配置起步,前端 chip/模型选择据此显示真实模型名。

租户 BYOK 自定义模型也并入列表(`owned_by="byok"`),让前端模型选择器
可见;平台列表在前、自定义追加去重,`default_model` 不变。

`/v1/models` 是公开端点,所以合并有前置条件(见 `_can_read_custom_models`):
必须带 X-Tenant-Id,且配置了内部密钥时还要有匹配的 X-Internal-Secret——
否则任何人传一个租户头就能枚举别家的模型清单。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.security import SECRET_HEADER, TENANT_HEADER
from fastapi import APIRouter, Header, Request

from llm_gateway.api.deps import resolve_custom_models
from llm_gateway.core.config import Settings

router = APIRouter(prefix="/models", tags=["models"])

TenantHeader = Annotated[str | None, Header(alias=TENANT_HEADER)]
SecretHeader = Annotated[str | None, Header(alias=SECRET_HEADER)]


def _can_read_custom_models(
    settings: Settings, tenant_id: str | None, internal_secret: str | None
) -> bool:
    """能否合并租户自定义模型:有租户 + (未配内部密钥 或 密钥常量时间匹配)。"""
    if not tenant_id:
        return False
    expected = settings.internal_secret
    if not expected:
        return True
    return bool(internal_secret) and secrets.compare_digest(internal_secret, expected)


def _model_item(model: str, owned_by: str) -> dict:
    """模型条目的对外形状(与平台模型保持一致,只换归属方)。"""
    return {"id": model, "object": "model", "owned_by": owned_by}


@router.get("")
async def list_models(
    request: Request,
    x_tenant_id: TenantHeader = None,
    x_internal_secret: SecretHeader = None,
) -> dict:
    settings: Settings = request.app.state.settings
    ids = list(
        settings.available_models
        or ([settings.default_model] if settings.default_model else [])
    )
    data = [_model_item(model, "beauty-moon") for model in ids]
    if _can_read_custom_models(settings, x_tenant_id, x_internal_secret):
        seen = set(ids)
        for model in await resolve_custom_models(request, x_tenant_id):
            if model in seen:  # 平台已列出的模型不重复追加
                continue
            seen.add(model)
            data.append(_model_item(model, "byok"))
    return {"object": "list", "default_model": settings.default_model, "data": data}
