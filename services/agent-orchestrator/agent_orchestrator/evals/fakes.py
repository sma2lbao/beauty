"""评测 harness 的测试替身:脚本化模型、合成危险工具、零网络守卫。

与 ``scripts/golden-run/golden_run.py`` 的口径一致(脚本不足即失败、DANGER
工具真实记录已发送参数),但放在包内:CLI 不能依赖 tests 目录,包内实现也
让 lint 与覆盖率门禁覆盖到这些替身(设计 docs/design/eval-and-cost-dashboard.md §2)。
"""

from __future__ import annotations

import json
import math
import socket
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from common.service_testkit.settings import build_settings
from common.testing import TestIdP

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services import models as models_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.llm import LLMReply, LLMStreamEvent, ToolCall
from agent_orchestrator.services.tools import ToolRisk, ToolSpec, build_tools

#: 脚本化模型在用量样本里的 model 字段(与真实模型名区分开)。
SCRIPTED_MODEL = "eval-scripted"
#: 评测账号(与黄金 run 同一口径:固定值让报告可复现)。
ACCOUNT_ID = "0123456789abcdef0123456789abcdef"

_IDP = TestIdP()
#: 评测请求的鉴权头(TestIdP 签发,进程内校验,零网络)。
ACCOUNT = _IDP.bearer(ACCOUNT_ID)


def token_verifier() -> Any:
    """TestIdP 的校验器,注入 create_app(token_verifier=...)。"""
    return _IDP.verifier()


class ScriptExhausted(RuntimeError):
    """脚本化模型没有更多回复:用例脚本写少了,必须显式失败。"""


class NetworkGuard:
    """把任何 AF_INET/AF_INET6 connect 记为违规并抛错,证明链路零网络。"""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._original = socket.socket

    def __enter__(self) -> NetworkGuard:
        guard = self
        original = self._original

        class GuardedSocket(original):  # type: ignore[misc, valid-type]
            def connect(self, address: Any) -> Any:
                if self.family in (socket.AF_INET, socket.AF_INET6):
                    guard.violations.append(f"connect({address!r})")
                    raise RuntimeError(
                        f"评测 harness 检测到网络连接:{address!r}(用例必须零网络)"
                    )
                return super().connect(address)

            def connect_ex(self, address: Any) -> Any:
                if self.family in (socket.AF_INET, socket.AF_INET6):
                    guard.violations.append(f"connect_ex({address!r})")
                    raise RuntimeError(
                        f"评测 harness 检测到网络连接:{address!r}(用例必须零网络)"
                    )
                return super().connect_ex(address)

        socket.socket = GuardedSocket  # type: ignore[assignment, misc]
        return self

    def __exit__(self, *exc_info: object) -> None:
        socket.socket = self._original  # type: ignore[assignment, misc]


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算(4 字符 ≈ 1 token),只要确定性、可跨机器复现即可。"""
    return max(1, math.ceil(len(text) / 4))


def _tool_call(item: Any, index: int, position: int) -> ToolCall:
    """脚本里的一条工具调用 -> ToolCall(arguments 统一成 JSON 字符串)。"""
    if not isinstance(item, Mapping) or not item.get("name"):
        raise ValueError(f"脚本第 {index + 1} 条第 {position + 1} 个工具调用缺少 name")
    arguments = item.get("arguments", "{}")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return ToolCall(
        id=f"call_{index + 1}_{position + 1}",
        name=str(item["name"]),
        arguments=arguments,
    )


def _reply_of(item: Any, index: int) -> LLMReply:
    """脚本元素 -> LLMReply:字符串是正文,映射是工具调用,LLMReply 原样。"""
    if isinstance(item, LLMReply):
        return item
    if isinstance(item, str):
        return LLMReply(content=item)
    if isinstance(item, Mapping) and isinstance(item.get("tool_calls"), Sequence):
        calls = item["tool_calls"]
        return LLMReply(
            content=str(item.get("content") or ""),
            tool_calls=tuple(
                _tool_call(entry, index, position)
                for position, entry in enumerate(calls)
            ),
        )
    raise ValueError(
        f"脚本第 {index + 1} 条格式不支持:只接受字符串 / LLMReply /"
        ' {"tool_calls": [...]}'
    )


class ScriptedLLM:
    """按调用次序吐脚本回复,并累计确定性的 token 估算。

    脚本元素三种写法(设计 §2.2):
    - 字符串:模型正文(流式时逐字符下发);
    - {"tool_calls": [{"name": ..., "arguments": ...}]}:工具调用(正文为空);
    - LLMReply:正文与工具调用同时给。

    每次调用都按"4 字符 ≈ 1 token"估算用量并上报,脚本化模型没有真实
    usage,评测报告与基线只能比确定性数字。
    """

    def __init__(self, script: Sequence[Any]) -> None:
        self.script: list[LLMReply] = [
            _reply_of(item, index) for index, item in enumerate(script)
        ]
        self.calls: list[list[dict[str, Any]]] = []
        self.tokens = 0

    def _take(self, messages: list[dict[str, Any]]) -> LLMReply:
        self.calls.append(messages)
        index = len(self.calls) - 1
        if index >= len(self.script):
            raise ScriptExhausted(
                f"模型脚本不足:第 {index + 1} 次调用没有对应回复"
                f"(脚本共 {len(self.script)} 条)"
            )
        reply = self.script[index]
        self._record_usage(messages, reply)
        return reply

    def _record_usage(self, messages: list[dict[str, Any]], reply: LLMReply) -> None:
        prompt = _estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str))
        completion = _estimate_tokens(reply.content) + sum(
            _estimate_tokens(call.arguments) for call in reply.tool_calls
        )
        total = prompt + completion
        self.tokens += total
        usage_service.record(
            usage_service.UsageSample(
                model=SCRIPTED_MODEL,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMReply:
        del model, tools, kwargs
        return self._take(messages)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async for event in self.stream_events(
            messages, model=model, tools=tools, **kwargs
        ):
            if event.text:
                yield event.text

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMStreamEvent]:
        """结构化流:正文逐字符吐出,工具调用放在收尾事件(对齐网关契约)。"""
        del response_format
        reply = await self.complete(messages, model=model, tools=tools, **kwargs)
        for char in reply.content:
            yield LLMStreamEvent(text=char)
        if reply.tool_calls:
            yield LLMStreamEvent(tool_calls=reply.tool_calls)


class FakeKnowledge:
    """假知识库:内存命中,记录检索调用(零网络)。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str, int]] = []

    async def search(self, account_id: str, query: str, limit: int) -> dict[str, Any]:
        self.queries.append((account_id, query, limit))
        hit = {
            "title": "本周日程",
            "score": 0.93,
            "content": "周一评审、周三发布、周五复盘。",
        }
        return {
            "query": query,
            "provider": "local",
            "model": "hashing-256",
            "hits": [hit][:limit],
        }


class FakeDangerTool:
    """合成 DANGER 工具:对外不可逆写操作(验证审批与确认闸,零网络)。"""

    name = "external_publish"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def _handle(self, args: dict[str, Any]) -> str:
        self.sent.append(args)
        return f"已发布:{args.get('title', '')}"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="对外不可逆写操作(评测 harness 用)",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            handler=self._handle,
            risk=ToolRisk.DANGER,
        )


def make_toolkit_factory(
    *, knowledge: FakeKnowledge, danger: FakeDangerTool
) -> Callable[[str], Any]:
    """基础工具(含假知识库)+ 合成 DANGER 工具的工具箱工厂。"""

    def _factory(account_id: str) -> Any:
        return build_tools(account_id, knowledge=knowledge).extended([danger.spec()])

    return _factory


def eval_settings(db_path: Path) -> Settings:
    """评测专用配置:每用例临时 sqlite、自足、零网络。

    与 tests/fixtures/app.py 同一口径:先把所有会连外部的开关钉死,再显式
    关掉会额外触发模型调用/后台任务的记忆、上下文、hooks 能力——否则用例
    的模型脚本会多消耗调用、断言的事件序列也会漂移。
    """
    return build_settings(
        Settings,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        auto_create_tables=True,
        sse_poll_interval_seconds=0.05,
        run_delta_streaming=False,
        llm_enabled=True,
        tools_enabled=True,
        plan_auto_approve=False,
        documents_enabled=False,
        knowledge_enabled=False,
        skill_execution_enabled=False,
        tool_service_enabled=False,
        account_service_url="",
        memory_enabled=False,
        memory_auto_extract_enabled=False,
        memory_consolidation_enabled=False,
        context_compaction_enabled=False,
        context_pipeline_enabled=False,
        context_skill_loading_enabled=False,
        context_offload_enabled=False,
        hooks_enabled=False,
        hooks_allow_http=False,
        llm_model=None,
        llm_available_models=[],
        llm_models_cache_seconds=0.0,
        artifact_download_enabled=False,
        mcp_enabled=False,
        mcp_server_enabled=False,
        mcp_oauth_enabled=False,
    )


def reset_process_state() -> None:
    """复位进程级状态:token 预算与模型缓存跨用例共享,必须清干净。"""
    usage_service.configure_budget(0)
    models_service.reset_models_cache()
