"""API v1 聚合路由。"""

from fastapi import APIRouter

from workspace.api.v1 import artifacts, objects, sandboxes, workspaces

api_router = APIRouter()
api_router.include_router(workspaces.router)
api_router.include_router(artifacts.router)
api_router.include_router(objects.router)
api_router.include_router(sandboxes.router)
