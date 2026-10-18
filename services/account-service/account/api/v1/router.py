"""API v1 聚合路由。"""

from fastapi import APIRouter

from account.api.v1 import account, api_keys, insights, oauth21

api_router = APIRouter()
api_router.include_router(account.router)
api_router.include_router(insights.router)
api_router.include_router(api_keys.router)
api_router.include_router(oauth21.router)
