"""API v1 聚合路由:运行上报/索引/详情/用例分页 + 基线。"""

from fastapi import APIRouter

from eval_service.api.v1 import baselines, runs

api_router = APIRouter()
api_router.include_router(runs.router)
api_router.include_router(baselines.router)
