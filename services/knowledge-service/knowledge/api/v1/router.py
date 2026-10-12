"""API v1 聚合路由。"""

from fastapi import APIRouter

from knowledge.api.v1 import knowledge

api_router = APIRouter()
api_router.include_router(knowledge.router)
