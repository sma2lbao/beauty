"""API v1 聚合路由。"""

from fastapi import APIRouter

from account.api.v1 import account

api_router = APIRouter()
api_router.include_router(account.router)
