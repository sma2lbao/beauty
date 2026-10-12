"""API v1 聚合路由。"""

from fastapi import APIRouter

from connector.api.v1 import connections

api_router = APIRouter()
api_router.include_router(connections.router)
