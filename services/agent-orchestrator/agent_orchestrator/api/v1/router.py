"""API v1 聚合路由(对外契约 /api/v1,见 docs/architecture.md §11)。"""

from fastapi import APIRouter

from agent_orchestrator.api.v1 import (
    conversations,
    mcp,
    notifications,
    outbox,
    runs,
    skills,
)

api_router = APIRouter()
api_router.include_router(runs.router)
api_router.include_router(notifications.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.attachments_router)
api_router.include_router(outbox.router)
api_router.include_router(skills.router)
api_router.include_router(mcp.router)
