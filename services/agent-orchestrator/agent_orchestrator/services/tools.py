"""工具注册表(架构 §6/§8):模型可调用的动作在这里注册、执行、回喂。

约定:

- 工具是模型接触外部世界的**唯一**通道;能力按**提供方(provider)**分组——
  本地文档库、企业知识库、代码库各产出一组 `ToolSpec`,`build_tools` 汇总;
  **没有配置的提供方不暴露工具**(能力即权限边界);
- 执行失败不抛给图,而是以 ``{"error": ...}`` 回喂模型让它决策(§6.1)——
  工具坏了不该把整条 run 炸掉;
- 每次调用产出 ToolCallRecord,由 services 层落 ``run.tool.called.v1`` 事件;
- 每个工具带**风险等级**(ToolRisk):只读与受控写自动执行,只有危险操作
  (对外/不可逆、未信任的第三方工具)才进人工确认闸(§6.2)。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from agent_orchestrator.services.code_tools import code_specs
from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.knowledge import Knowledge

MAX_DOC_LIMIT = 50
MAX_KNOWLEDGE_LIMIT = 10
MAX_DOC_CHARS = 20_000  # 回喂给模型的正文上限(防提示词爆炸)


class ToolRisk(StrEnum):
    """工具风险等级:决定要不要人工确认(§6.2)。

    - ``READ``:只读、无副作用,直接执行;
    - ``WRITE``:受控范围内的副作用(工作区文件、沙箱内技能执行、已信任的
      MCP server),自动执行——每一步都问人会把 agent 退化成人工点击机;
    - ``DANGER``:对外或不可逆的操作(未信任 MCP server 的第三方工具),
      执行前必须人工放行。
    """

    READ = "read"
    WRITE = "write"
    DANGER = "danger"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    risk: ToolRisk = ToolRisk.READ

    @property
    def requires_confirmation(self) -> bool:
        """仅危险操作需要人工确认(执行闸的唯一判据)。"""
        return self.risk is ToolRisk.DANGER


#: 外部工具工厂:按租户在调用边界现取(MCP 注册表 / skill_run)。
#: run 执行图与 chat 直接对话共用同一份——对话里能不能执行由 spec 的风险
#: 等级决定(DANGER 不执行,chat 没有审批流,§6.7)。
ToolExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的审计记录(状态里累积,services 层落事件)。"""

    name: str
    arguments: str
    ok: bool
    detail: str


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _int_arg(args: dict[str, Any], key: str, default: int, *, maximum: int) -> int:
    try:
        value = int(args.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


class ToolRegistry:
    """按名字执行工具:未知工具/坏 JSON/执行异常一律回喂错误文本。"""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    @property
    def has_dangerous(self) -> bool:
        """注册表里是否有 DANGER 级工具(计划审批闸据此决定是否自动放行)。"""
        return any(spec.risk is ToolRisk.DANGER for spec in self._specs.values())

    def extended(self, specs: list[ToolSpec]) -> ToolRegistry:
        """合并外部工具(如 MCP);同名后者覆盖前者。"""
        return ToolRegistry([*self._specs.values(), *specs])

    def restricted(self, allowed: list[str]) -> ToolRegistry:
        """按 Skill 的 tools 白名单裁剪(能力边界);未知/未列出工具不暴露。"""
        allow = set(allowed)
        return ToolRegistry(
            [spec for name, spec in self._specs.items() if name in allow]
        )

    def risk(self, name: str) -> ToolRisk:
        """工具风险等级;未知工具按最严处理(绝不默认放行)。"""
        spec = self._specs.get(name)
        return spec.risk if spec is not None else ToolRisk.DANGER

    def requires_confirmation(self, name: str) -> bool:
        """危险操作:execute 不得直接执行,必须交确认闸(§6.2)。"""
        return self.risk(name) is ToolRisk.DANGER

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI tools 格式(网关原样透传给 litellm)。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    async def execute(self, name: str, arguments: str) -> str:
        spec = self._specs.get(name)
        if spec is None:
            return _error(f"未知工具:{name}")
        try:
            args = json.loads(arguments or "{}")
        except ValueError as exc:
            return _error(f"工具参数不是合法 JSON:{exc}")
        if not isinstance(args, dict):
            return _error("工具参数必须是 JSON 对象")
        try:
            return await spec.handler(args)
        except Exception as exc:
            return _error(f"{type(exc).__name__}: {exc}")


def document_specs(documents: DocumentLibrary) -> list[ToolSpec]:
    """本地文档库:列文档 / 读文档(只读,目录受限)。"""

    async def list_documents(args: dict[str, Any]) -> str:
        entries = await documents.list(
            limit=_int_arg(args, "limit", 20, maximum=MAX_DOC_LIMIT)
        )
        return json.dumps(
            {"documents": [asdict(entry) for entry in entries]}, ensure_ascii=False
        )

    async def read_document(args: dict[str, Any]) -> str:
        path = str(args.get("path") or "")
        if not path:
            return _error("缺少 path(可先调 list_documents 获取)")
        text = await documents.read(path)
        return json.dumps(
            {
                "path": path,
                "chars": len(text),
                "truncated": len(text) > MAX_DOC_CHARS,
                "content": text[:MAX_DOC_CHARS],
            },
            ensure_ascii=False,
        )

    return [
        ToolSpec(
            name="list_documents",
            description="列出工作区文档库中的文档(相对路径/大小/修改时间,只读)",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_DOC_LIMIT,
                        "description": "返回条数,默认 20",
                    }
                },
                "additionalProperties": False,
            },
            handler=list_documents,
        ),
        ToolSpec(
            name="read_document",
            description="读取文档库中某个文档的正文(只读,超长会截断)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "list_documents 返回的相对路径",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_document,
        ),
    ]


def knowledge_specs(tenant_id: str, knowledge: Knowledge) -> list[ToolSpec]:
    """企业知识库检索(只读):让模型先查资料再回答。"""

    async def search_knowledge(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return _error("缺少 query")
        result = await knowledge.search(
            tenant_id, query, _int_arg(args, "limit", 5, maximum=MAX_KNOWLEDGE_LIMIT)
        )
        hits = [
            {
                "title": hit.get("title"),
                "score": hit.get("score"),
                "content": hit.get("content"),
            }
            for hit in result.get("hits") or []
        ]
        return json.dumps(
            {
                "query": query,
                "provider": result.get("provider"),
                "model": result.get("model"),
                "hits": hits,
            },
            ensure_ascii=False,
        )

    return [
        ToolSpec(
            name="search_knowledge",
            description="在团队知识库中做向量检索,返回最相关的资料片段(只读)",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题/关键词"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_KNOWLEDGE_LIMIT,
                        "description": "返回片段数,默认 5",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_knowledge,
        )
    ]


def build_tools(
    tenant_id: str,
    *,
    documents: DocumentLibrary | None = None,
    knowledge: Knowledge | None = None,
    code_roots: list[str] | None = None,
) -> ToolRegistry:
    """汇总已配置提供方的工具;未配置的能力不暴露(能力即权限边界)。"""
    specs: list[ToolSpec] = []
    if documents is not None:
        specs += document_specs(documents)
    if knowledge is not None:
        specs += knowledge_specs(tenant_id, knowledge)
    if code_roots:
        specs += code_specs(code_roots)
    return ToolRegistry(specs)
