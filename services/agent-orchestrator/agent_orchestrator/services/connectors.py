"""显式引用的连接器(P1):按 provider 校验并生成提示词 note。

连接器是**能力绑定**(不是内容引用):用户选的是"这次只用这些连接器",
正文 token 只是可读标记;真正生效靠请求里的 connector_providers 与
ToolContext,最终由 RemoteToolkit 的工具过滤落地。

口径:

- 空选择 = 不限制(沿用"全部已连接工具"的既有行为),零下游请求;
- provider 必须在账号的工具目录里出现过(有连接器工具),否则 404
  connector.not_found——连接断开 / 没有可用工具都归到这一条;
- 目录拉取失败保留 UpstreamError(502),不把基础设施故障伪装成 404;
- 不校验单条 connection 状态:目录里有该 provider 的工具即可用。
"""

from __future__ import annotations

from typing import Any

from common.errors import ConflictError, NotFoundError

#: 一次最多显式引用多少个 provider(与 schema 的 max_length 对齐)。
MAX_CONNECTOR_PROVIDERS = 10


def connector_reference_note(providers: list[str]) -> str:
    """生成"本次只用这些连接器"的系统提示片段;空列表返回空串。"""
    if not providers:
        return ""
    names = "、".join(providers)
    return (
        f"用户在本次消息里明确引用了连接器:{names}。"
        "本次只应使用这些连接器提供的工具;未列出的连接器工具不可用。"
    )


def _available_providers(tools: Any) -> set[str]:
    """从工具目录里收集出现过的连接器 provider。"""
    available: set[str] = set()
    for tool in tools:
        connector = getattr(tool, "connector", None)
        if not isinstance(connector, dict):
            continue
        provider = connector.get("provider")
        if isinstance(provider, str) and provider:
            available.add(provider)
    return available


async def resolve_connector_providers(
    account_id: str,
    providers: list[str],
    *,
    tool_client: Any,
    settings: Any,
) -> list[str]:
    """校验显式引用的 provider;返回去重保序后的列表。

    未知 provider → 404(详情带 providers,前端据此提示去连接器页检查);
    能力未启用 → 409。目录拉取异常原样向上抛(UpstreamError)。
    """
    if not providers:
        return []
    if not getattr(settings, "connector_providers_enabled", True):
        raise ConflictError(
            "connector.unavailable",
            "连接器引用能力未启用",
            details={"providers": providers},
        )
    if tool_client is None:
        raise ConflictError(
            "connector.unavailable",
            "工具服务未启用,无法校验连接器",
            details={"providers": providers},
        )
    if len(providers) > MAX_CONNECTOR_PROVIDERS:
        raise ConflictError(
            "connector.limit_exceeded",
            f"一次最多引用 {MAX_CONNECTOR_PROVIDERS} 个连接器",
            details={"max": MAX_CONNECTOR_PROVIDERS},
        )
    tools, _version = await tool_client.list_tools(account_id)
    available = _available_providers(tools)
    ordered: list[str] = []
    seen: set[str] = set()
    for provider in providers:
        if provider in seen:
            continue
        seen.add(provider)
        ordered.append(provider)
    missing = [provider for provider in ordered if provider not in available]
    if missing:
        raise NotFoundError(
            "connector.not_found",
            "连接器未连接或没有可用工具",
            details={"providers": missing},
        )
    return ordered
