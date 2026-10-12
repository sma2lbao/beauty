"""API v1 聚合路由。"""

from fastapi import APIRouter

from llm_gateway.api.v1 import completions, embeddings

api_router = APIRouter()
api_router.include_router(completions.router)
api_router.include_router(embeddings.router)
