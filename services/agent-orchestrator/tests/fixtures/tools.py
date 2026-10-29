"""工具替身:FakeDangerTool、ask_question 脚本构造与假工具注册表。

- FakeDangerTool 是合成的 DANGER 工具(对外不可逆写操作),用来验证确认闸;
- ask_question 的 schema 与默认两问供 interrupt payload 断言;
- FakeInteractionToolkit 覆盖 ToolkitPort 的 kind/validate_call/可见性钩子。
"""

from __future__ import annotations

import json
from typing import Any

from common.tools import validate_arguments

from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolRisk, ToolSpec


class FakeDangerTool:
    """合成 DANGER 工具:对外不可逆写操作(验证确认闸,不绑定具体产品能力)。"""

    name = "external_publish"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    async def _handle(self, args: dict[str, Any]) -> str:
        self.calls.append(args)
        if self.fail:
            raise RuntimeError("发布通道挂了")
        self.sent.append(args)
        return f"已发布:{args.get('title', '')}"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="对外不可逆写操作(测试用)",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            handler=self._handle,
            risk=ToolRisk.DANGER,
        )


#: ask_question 的入参 schema(tool-service 目录描述的简化版,够测参数预校验)。
ASK_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "header": {"type": "string", "maxLength": 12},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "maxLength": 80},
                                "description": {
                                    "type": "string",
                                    "maxLength": 300,
                                },
                            },
                            "required": ["label"],
                        },
                    },
                    "multi_select": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        "allow_custom": {"type": "boolean"},
    },
    "required": ["questions"],
}

#: 默认两问:断言 interrupt payload 原样带出 questions。
DEFAULT_ASK_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "汇报周期?",
        "header": "周期",
        "options": [{"label": "每周"}, {"label": "每月"}],
    },
    {
        "question": "同步时间?",
        "header": "时间",
        "options": [{"label": "周一"}, {"label": "周三"}],
    },
]


def ask_reply(
    *,
    call_id: str = "call_ask",
    questions: list[dict[str, Any]] | None = None,
    allow_custom: bool | None = None,
) -> LLMReply:
    """构造一次 ask_question 工具调用(LLM 侧脚本用)。"""
    payload: dict[str, Any] = {"questions": questions or DEFAULT_ASK_QUESTIONS}
    if allow_custom is not None:
        payload["allow_custom"] = allow_custom
    call = ToolCall(
        id=call_id,
        name="ask_question",
        arguments=json.dumps(payload, ensure_ascii=False),
    )
    return LLMReply(content="", tool_calls=(call,))


class FakeInteractionToolkit:
    """假工具注册表:ask_question 是 interaction 类,其余工具是 function 类。

    - kind()/validate_call() 覆盖 ToolkitPort 的新钩子;
    - with_interaction() 打开交互工具可见性——execute 节点会调它,task 图因此
      看得到 ask_question;chat 只读路径不调,自然看不到;
    - execute 一律回 tool.not_supported:ask 不该走执行路径,测试据此断言
      "interrupt 前零副作用"。
    """

    def __init__(self, *, interaction: bool = False) -> None:
        self._interaction = interaction
        self._allowed: frozenset[str] | None = None
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.validated: list[tuple[str, str]] = []

    def _clone(self, *, interaction: bool | None = None) -> FakeInteractionToolkit:
        clone = FakeInteractionToolkit(
            interaction=self._interaction if interaction is None else interaction
        )
        clone._allowed = self._allowed
        clone.executed = self.executed
        clone.validated = self.validated
        return clone

    async def ensure_ready(self) -> None:
        return None

    def __len__(self) -> int:
        return len(self.names)

    @property
    def names(self) -> list[str]:
        names = ["ask_question"] if self._interaction else []
        if self._allowed is not None:
            names = [name for name in names if name in self._allowed]
        return names

    @property
    def has_dangerous(self) -> bool:
        return False

    def risk(self, name: str) -> ToolRisk:
        return ToolRisk.READ

    def kind(self, name: str) -> str:
        return "interaction" if name == "ask_question" else "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        self.validated.append((name, arguments))
        if name != "ask_question":
            return []
        try:
            payload = json.loads(arguments or "{}")
        except ValueError as exc:
            return [f"工具参数不是合法 JSON: {exc}"]
        if not isinstance(payload, dict):
            return ["工具参数必须是 JSON 对象"]
        return [
            error.message for error in validate_arguments(ASK_QUESTION_SCHEMA, payload)
        ]

    def schemas(self) -> list[dict[str, Any]]:
        if not self._interaction:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "ask_question",
                    "description": "向用户提问(测试用)",
                    "parameters": ASK_QUESTION_SCHEMA,
                },
            }
        ]

    def extended(self, specs: list[ToolSpec]) -> FakeInteractionToolkit:
        return self

    def restricted(self, allowed: list[str]) -> FakeInteractionToolkit:
        clone = self._clone()
        clone._allowed = frozenset(allowed)
        return clone

    def with_interaction(self) -> FakeInteractionToolkit:
        return self._clone(interaction=True)

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        self.executed.append((name, {"arguments": arguments, "approved": approved}))
        return json.dumps(
            {
                "ok": False,
                "error": {"code": "tool.not_supported", "message": "测试不执行工具"},
            },
            ensure_ascii=False,
        )
