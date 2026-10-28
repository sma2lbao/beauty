"""模型目录端点(P1-G,见 docs/design/model-routing.md §6.3)。

纯粹是网关 ``GET /v1/models`` 的投影 + 60s 缓存;这里不做路由决策,
模型校验由写入口(建 run / 会话 / 发消息)调 services.models.validate_model。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from agent_orchestrator.api.deps import get_llm_client, get_settings
from agent_orchestrator.schemas.models import ModelOptionRead, ModelsRead
from agent_orchestrator.services import models as models_service
from agent_orchestrator.services.llm import LLMClient

router = APIRouter(prefix="/models", tags=["models"])

LLM = Annotated[LLMClient | None, Depends(get_llm_client)]
SettingsDep = Annotated[Any, Depends(get_settings)]


@router.get("", response_model=ModelsRead)
async def list_models(llm: LLM, settings: SettingsDep) -> ModelsRead:
    """可用模型:网关不可达/未启用时回退 LLM_AVAILABLE_MODELS(空则空列表)。"""
    options = await models_service.available_models(llm, settings)
    return ModelsRead(
        default_model=settings.llm_model,
        models=[
            ModelOptionRead(id=option.id, owned_by=option.owned_by)
            for option in options
        ],
    )
