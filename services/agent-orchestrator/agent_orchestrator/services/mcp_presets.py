"""内置 MCP 预设:把「官方 MCP 端点 + 一键授权」需要的信息收敛到一处。

预设只是**静态描述**(key/name/url/文档/只读工具名前缀),不碰数据库:
创建 server 的幂等逻辑在 services/mcp_servers.ensure_preset_server,
风险分级用 preset_for_url + read_prefixes(见 services/mcp.tool_specs)。

官方端点(已实测):RFC 9728 PRM → AS 元数据 → DCR(RFC 7591)→ PKCE S256 →
refresh_token,现有 mcp_oauth 全链路可直接复用。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.errors import NotFoundError

NOTION_MCP_URL = "https://mcp.notion.com/mcp"


@dataclass(frozen=True)
class MCPPreset:
    """一个内置 MCP 预设(只读声明,不可运行时修改)。"""

    key: str
    name: str
    url: str
    description: str
    docs_url: str | None
    #: 只读工具名前缀:命中即按 READ 处理(server 未声明 readOnlyHint 时的兜底)
    read_prefixes: tuple[str, ...] = ()


PRESETS: tuple[MCPPreset, ...] = (
    MCPPreset(
        key="notion",
        name="Notion",
        url=NOTION_MCP_URL,
        description=(
            "Notion 官方 MCP:搜索与读取页面/数据库、更新内容、查看日历,"
            "按工具粒度分级审批"
        ),
        docs_url="https://developers.notion.com/docs/mcp",
        read_prefixes=(
            "notion-search",
            "notion-fetch",
            "notion-list-",
            "notion-get-",
            "notion-query-",
            "notion-read-",
            "notion-view-",
            "notion-download-",
            "notion-wait-",
            "notion-calendar-list-",
        ),
    ),
)


def list_presets() -> list[MCPPreset]:
    """全部预设(顺序稳定,前端按此渲染预设连接器卡片)。"""
    return list(PRESETS)


def _normalize_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def preset_for_key(key: str) -> MCPPreset:
    """按 key 找预设;未知 key 一律 404(不泄漏还有哪些 key)。"""
    for preset in PRESETS:
        if preset.key == key:
            return preset
    raise NotFoundError("mcp.preset_not_found", "MCP 预设不存在")


def preset_for_url(url: str | None) -> MCPPreset | None:
    """按 server.url 反查预设;忽略末尾斜杠与首尾空白。"""
    normalized = _normalize_url(url)
    if not normalized:
        return None
    for preset in PRESETS:
        if _normalize_url(preset.url) == normalized:
            return preset
    return None
