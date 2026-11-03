"""v1 路由汇总。"""

from __future__ import annotations

from fastapi import APIRouter

from connector.api.v1 import connections, connectors

api_router = APIRouter()
api_router.include_router(connectors.router)
api_router.include_router(connections.router)

__all__ = ["api_router"]
