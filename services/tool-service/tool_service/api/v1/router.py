"""API v1 聚合路由:工具目录/执行 + 审计查询。"""

from fastapi import APIRouter

from tool_service.api.v1 import tool_calls, tools

api_router = APIRouter()
api_router.include_router(tools.router)
api_router.include_router(tool_calls.router)
