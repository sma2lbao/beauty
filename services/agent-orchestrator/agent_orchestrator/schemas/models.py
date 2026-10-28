"""模型目录 DTO(P1-G,见 docs/design/model-routing.md §6.3)。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelOptionRead(BaseModel):
    """可选模型:前端只用来展示/选择,语义由网关决定。"""

    id: str
    owned_by: str


class ModelsRead(BaseModel):
    """GET /api/v1/models:默认模型 + 可用模型(网关不可达时为配置回退)。"""

    default_model: str | None = None
    models: list[ModelOptionRead] = Field(default_factory=list)
