"""响应归一化:用量、增量正文、tool_calls 装配(litellm 对象式/dict 式都兼容)。"""

from __future__ import annotations

from typing import Any

from llm_gateway import metrics
from llm_gateway.schemas.completions import ToolCall, ToolFunction, UsageOut
from llm_gateway.services.completion_request import _get


def _normalize_tool_calls(message: Any) -> list[ToolCall]:
    """litellm 的 tool_calls 对象/字典 → 归一化 ToolCall;缺字段保守跳过。"""
    calls: list[ToolCall] = []
    for raw in _get(message, "tool_calls") or []:
        function = _get(raw, "function")
        if function is None:
            continue
        calls.append(
            ToolCall(
                id=str(_get(raw, "id") or ""),
                function=ToolFunction(
                    name=str(_get(function, "name") or ""),
                    arguments=str(_get(function, "arguments") or "{}"),
                ),
            )
        )
    return calls


def _record_usage(usage: UsageOut | None, model: str) -> None:
    """token 用量按 input/output 两个方向计入 llm_tokens_total。"""
    if usage is None:
        return
    metrics.observe_llm_tokens(
        model=model, direction="input", tokens=usage.prompt_tokens
    )
    metrics.observe_llm_tokens(
        model=model, direction="output", tokens=usage.completion_tokens
    )


def _usage(raw: Any) -> UsageOut | None:
    if raw is None:
        return None
    return UsageOut(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw, "total_tokens", 0) or 0,
    )


def _delta_text(chunk: Any) -> str:
    """流片里的增量正文;litellm 对象式与 dict 式都要兼容。"""
    choices = _get(chunk, "choices") or []
    if not choices:
        return ""
    delta = _get(choices[0], "delta")
    return str(_get(delta, "content") or "")


class _ToolCallAssembler:
    """流式 tool_calls 装配:litellm 按 index 分片下发,这里合并成完整调用。

    分片规则(OpenAI 兼容):``id``/``name`` 通常只在首片出现,``arguments`` 是字符串
    片段、按到达顺序拼接;对象式与 dict 式分片都要兼容。半截调用不单独下发——
    完整列表只随收尾 done 片整包带出,中途失败时不会有半截 JSON 漏给调用方。
    """

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def add(self, chunk: Any) -> None:
        """吸收一片流式响应里的 tool_calls 分片(该片没有则跳过)。"""
        choices = _get(chunk, "choices") or []
        if not choices:
            return
        delta = _get(choices[0], "delta")
        for raw in _get(delta, "tool_calls") or []:
            index = _get(raw, "index")
            # index 缺省(部分提供商单调用不分片标号)按 0 处理
            slot = self._parts.setdefault(
                index if isinstance(index, int) else 0,
                {"id": "", "name": "", "arguments": ""},
            )
            call_id = _get(raw, "id")
            if call_id:
                slot["id"] = str(call_id)
            function = _get(raw, "function")
            name = _get(function, "name")
            if name:
                slot["name"] = str(name)
            arguments = _get(function, "arguments")
            if arguments:
                slot["arguments"] += str(arguments)

    def calls(self) -> list[ToolCall] | None:
        """装配结果:按 index 排序;没有任何分片时返回 None(调用方据 None 省键)。"""
        if not self._parts:
            return None
        return [
            ToolCall(
                id=slot["id"],
                function=ToolFunction(
                    name=slot["name"], arguments=slot["arguments"] or "{}"
                ),
            )
            for _, slot in sorted(self._parts.items())
        ]
