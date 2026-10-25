"""FastAPI 依赖:容器、DB session 与租户解析。

租户来源(架构 §10):
- 鉴权开启(\`\`AUTH_ENABLED=true\`\`):校验 \`\`Authorization: Bearer <access token>\`\`,
  租户从 token claim 取——**不可信则 401,绝不回退到请求头**;
- 鉴权显式关闭(\`\`AUTH_ENABLED=false\`\`,不推荐,仅特殊部署):沿用 \`\`X-Tenant-Id\`\`
  临时头,缺失即 401。

接入 IdP 后端点零改动:只换这里的实现。所有外部依赖统一从 AppContainer
取(api 层不再直接读 app.state,也不再自己建仓储)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from database.session import session_scope
from fastapi import Header, Request
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.container import AppContainer
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace


def get_container(request: Request) -> AppContainer:
    """进程装配容器:api 层获取外部依赖的唯一入口。"""
    return request.app.state.container


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """一请求一事务;顺带把 outbox recorder 挂到 session.info。

    \`\`session.info\`\` 是 SQLAlchemy 官方的用户数据挂点:业务层(append_event)据此
    在不改函数签名的前提下,把事件写进**同一事务**的发件箱。
    """
    container = get_container(request)
    assert container.session_factory is not None
    async with session_scope(container.session_factory) as session:
        recorder = container.outbox_recorder
        if recorder is not None:
            session.info["outbox"] = recorder
        yield session


def get_settings(request: Request):
    """当前进程配置(附件大小上限等端点参数)。"""
    return get_container(request).settings


def get_skills(request: Request) -> SkillRegistry:
    """启动时加载的 Skill 注册表。"""
    return get_container(request).skills


def get_run_graph(request: Request) -> CompiledStateGraph:
    """启动时装配的 run 执行图。"""
    return get_container(request).run_graph


def get_llm_client(request: Request) -> LLMClient | None:
    """模型客户端;未启用(LLM_ENABLED=false)时为 None。

    直接对话(§6.7)与执行图共用同一客户端;为 None 时没有"直接对话"能力,
    消息只能走任务路径(确定性桩)。
    """
    return get_container(request).llm_client


def get_toolkit_factory(request: Request):
    """工具工厂;未启用(TOOLS_ENABLED=false)时为 None。

    run 执行图与 chat 直接对话(§6.8 对话内 function-calling)共用同一工厂。
    """
    return get_container(request).toolkit_factory


def get_tool_extras_factory(request: Request):
    """外部工具(MCP / skill_run)工厂;与 run 执行图共用同一个(§6.8)。

    未启用 MCP/skill 执行时为 None:chat 只有内置工具,行为不变。
    """
    return get_container(request).tool_extras_factory


def get_workspace_client(request: Request) -> Workspace | None:
    """按配置装配的工作区客户端;未开启为 None(不镜像)。"""
    return get_container(request).workspace_client


def get_artifact_store(request: Request) -> ArtifactStore | None:
    """按配置装配的交付物存储;database 模式为 None(内容在行里)。"""
    return get_container(request).artifact_store


def get_file_downloader(request: Request):
    """执行时抓取工具外链文件的下载器;未开启为 None。"""
    return get_container(request).artifact_downloader


def get_mcp_stdio_pool(request: Request) -> StdioSandboxPool | None:
    """stdio MCP 沙箱池;未开启(mcp_stdio_enabled=false)为 None。

    为 None 时 test 端点对 stdio server 明确报"需要启用沙箱",tool_specs 跳过
    stdio server——不静默变成"工具消失"。
    """
    return get_container(request).mcp_stdio_pool


async def get_tenant_id(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """租户解析:IdP token 优先,鉴权关闭时才认临时头。"""
    verifier: JwtVerifier | None = get_container(request).token_verifier
    if verifier is None:
        if not x_tenant_id:
            raise UnauthorizedError(
                "auth.header_missing",
                "缺少 X-Tenant-Id(鉴权未开启时的临时头)",
            )
        bind_request_context(tenant_id=x_tenant_id)
        return x_tenant_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(tenant_id=principal.tenant_id, user_id=principal.subject)
    return principal.tenant_id
