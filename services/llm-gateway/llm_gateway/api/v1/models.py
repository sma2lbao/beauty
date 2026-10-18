"""模型列表(M0-11):静态配置起步,前端 chip/模型选择据此显示真实模型名。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from llm_gateway.core.config import Settings

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
async def list_models(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    ids = settings.available_models or (
        [settings.default_model] if settings.default_model else []
    )
    return {
        "object": "list",
        "default_model": settings.default_model,
        "data": [
            {
                "id": model,
                "object": "model",
                "owned_by": "beauty-moon",
            }
            for model in ids
        ],
    }
