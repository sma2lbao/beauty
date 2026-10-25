"""API v1 聚合路由(对外契约 /api/v1,见 docs/architecture.md §11)。"""

from fastapi import APIRouter

from agent_orchestrator.api.v1 import (
    approval_policies,
    conversation_messages,
    conversations,
    mcp,
    memory,
    notifications,
    outbox,
    run_actions,
    run_artifacts,
    runs,
    skills,
)

api_router = APIRouter()
api_router.include_router(runs.router)
api_router.include_router(run_actions.router)
api_router.include_router(run_artifacts.router)
api_router.include_router(notifications.router)
api_router.include_router(conversations.router)
api_router.include_router(conversation_messages.router)
api_router.include_router(conversations.attachments_router)
api_router.include_router(outbox.router)
api_router.include_router(skills.router)
api_router.include_router(mcp.router)
api_router.include_router(approval_policies.router)
api_router.include_router(memory.router)
