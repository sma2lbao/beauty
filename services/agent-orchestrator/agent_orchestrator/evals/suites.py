"""评测套件与用例 schema(设计 §2.2):纯数据,runner 只按字段驱动。

用例来源两类:
- 内置套件 ``build_suites()``(chat-direct / task-approval / tool-policy);
- 外部用例集 ``--suite-json`` 或者 ``--suite`` 指定名字。

形状不对立即抛 ValueError(中文原因,CLI 直接打印),不做静默兜底——
用例写错却跑出"通过"是最坏的评测失败。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

KIND_CHAT = "chat"
KIND_TASK = "task"
KINDS = (KIND_CHAT, KIND_TASK)

STEP_CREATE_CONVERSATION = "create_conversation"
STEP_SEND_CHAT = "send_chat"
STEP_CREATE_RUN = "create_run"
STEP_APPROVE_PLAN = "approve_plan"
STEP_CONFIRM_ALL = "confirm_all"
STEP_WAIT_TERMINAL = "wait_terminal"
STEPS = (
    STEP_CREATE_CONVERSATION,
    STEP_SEND_CHAT,
    STEP_CREATE_RUN,
    STEP_APPROVE_PLAN,
    STEP_CONFIRM_ALL,
    STEP_WAIT_TERMINAL,
)

#: 未显式写 steps 时按 kind 推导(§2.2)。
DEFAULT_STEPS: dict[str, tuple[str, ...]] = {
    KIND_CHAT: (STEP_CREATE_CONVERSATION, STEP_SEND_CHAT),
    KIND_TASK: (
        STEP_CREATE_RUN,
        STEP_APPROVE_PLAN,
        STEP_CONFIRM_ALL,
        STEP_WAIT_TERMINAL,
    ),
}

#: 允许出现在 assert 里的键(拼错必须显式失败,不能当没写)。
ASSERT_KEYS = frozenset(
    {
        "run_status",
        "events_include",
        "events_exclude",
        "tool_calls",
        "reply_contains",
        "min_events",
        "max_events",
        "deliverables",
        "max_total_tokens",
    }
)


@dataclass(frozen=True)
class Case:
    """一条评测用例(§2.2):用例是数据,不写代码分支。"""

    id: str
    kind: str
    goal: str
    llm_script: tuple[Any, ...] = ()
    steps: tuple[str, ...] = ()
    expect: Mapping[str, Any] = field(default_factory=dict)
    #: tool-policy 套件需要在建 run 前写审批策略表(契约 §2.3 的扩展字段,
    #: 形状同 PUT /api/v1/approval-policies 的 policies 列表)。
    policies: tuple[Mapping[str, Any], ...] = ()
    title: str = ""

    def resolved_steps(self) -> tuple[str, ...]:
        """显式 steps 优先;没写按 kind 的默认步骤走。"""
        return self.steps or DEFAULT_STEPS[self.kind]

    def resolved_title(self) -> str:
        return self.title or self.id

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, where: str = "用例") -> Case:
        case_id = payload.get("id")
        kind = payload.get("kind", KIND_TASK)
        goal = payload.get("goal")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{where}:缺少非空 id")
        if kind not in KINDS:
            raise ValueError(f"{where} {case_id}:kind 只支持 chat/task,实际 {kind!r}")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError(f"{where} {case_id}:缺少非空 goal")
        script = payload.get("llm_script") or []
        if not isinstance(script, Sequence) or isinstance(script, (str, bytes)):
            raise ValueError(f"{where} {case_id}:llm_script 必须是列表")
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes)):
            raise ValueError(f"{where} {case_id}:steps 必须是列表")
        steps = tuple(str(step) for step in raw_steps)
        unknown_steps = [step for step in steps if step not in STEPS]
        if unknown_steps:
            raise ValueError(
                f"{where} {case_id}:未知步骤 {unknown_steps},支持 {list(STEPS)}"
            )
        expect = payload.get("assert") or {}
        if not isinstance(expect, Mapping):
            raise ValueError(f"{where} {case_id}:assert 必须是对象")
        unknown_keys = sorted(set(expect) - ASSERT_KEYS)
        if unknown_keys:
            raise ValueError(
                f"{where} {case_id}:assert 里的键不支持 {unknown_keys},"
                f"支持 {sorted(ASSERT_KEYS)}"
            )
        policies = payload.get("policies") or []
        if not isinstance(policies, Sequence) or isinstance(policies, (str, bytes)):
            raise ValueError(f"{where} {case_id}:policies 必须是列表")
        for policy in policies:
            if not isinstance(policy, Mapping) or not policy.get("tool"):
                raise ValueError(f"{where} {case_id}:policies 每项都要有 tool")
        return cls(
            id=case_id.strip(),
            kind=str(kind),
            goal=goal,
            llm_script=tuple(script),
            steps=steps,
            expect=dict(expect),
            policies=tuple(dict(policy) for policy in policies),
            title=str(payload.get("title") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化回 JSON 形状(键名与契约一致:assert 而非 expect)。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "goal": self.goal,
            "title": self.title,
            "llm_script": list(self.llm_script),
            "steps": list(self.steps),
            "assert": dict(self.expect),
            "policies": [dict(policy) for policy in self.policies],
        }


@dataclass(frozen=True)
class Suite:
    """一个评测套件:一组同主题用例。"""

    name: str
    cases: tuple[Case, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "cases": [case.to_dict() for case in self.cases]}


def suite_from_dict(payload: Mapping[str, Any], *, where: str = "套件") -> Suite:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}:缺少非空 name")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
        raise ValueError(f"{where} {name}:cases 必须是列表")
    cases = tuple(
        Case.from_dict(case, where=f"{where} {name} 的用例")
        for case in raw_cases
        if isinstance(case, Mapping)
    )
    if len(cases) != len(raw_cases):
        raise ValueError(f"{where} {name}:cases 里每项必须是对象")
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"{where} {name}:用例 id 重复 {case.id}")
        seen.add(case.id)
    return Suite(name=name.strip(), cases=cases)


def suites_from_payload(payload: Any, *, where: str = "外部用例集") -> list[Suite]:
    """--suite-json 的内容 -> 套件列表(支持单个对象或对象数组)。"""
    if isinstance(payload, Mapping):
        return [suite_from_dict(payload, where=where)]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return [
            suite_from_dict(item, where=where)
            for item in payload
            if isinstance(item, Mapping)
        ]
    raise ValueError(f"{where}:必须是套件对象或套件数组")


def build_suites() -> list[Suite]:
    """三个内置套件(§2.3):直答、任务审批链路、策略自动放行。"""
    return [
        Suite(name="chat-direct", cases=_chat_direct_cases()),
        Suite(name="task-approval", cases=_task_approval_cases()),
        Suite(name="tool-policy", cases=_tool_policy_cases()),
    ]


def _chat_direct_cases() -> tuple[Case, ...]:
    return (
        Case(
            id="direct-answer",
            kind=KIND_CHAT,
            goal="你好,帮我看看今天该做什么",
            llm_script=("你好!我可以帮你整理本周日程。",),
            expect={
                # 直答不建 run:run 相关指标全空,并且不能出现建 run 事件。
                "run_status": None,
                "reply_contains": "整理本周日程",
                "events_exclude": ["run.created.v1"],
            },
        ),
    )


def _task_approval_cases() -> tuple[Case, ...]:
    return (
        Case(
            id="approve-then-complete",
            kind=KIND_TASK,
            goal="整理本周日程并发布说明",
            llm_script=(
                "# 执行计划\n- [ ] 检索日程\n- [ ] 发布说明",
                {
                    "tool_calls": [
                        {
                            "name": "external_publish",
                            "arguments": {"title": "周报"},
                        }
                    ]
                },
                "已发布周报。",
                "# 交付物\n\n周报已发布。",
            ),
            steps=(
                STEP_CREATE_RUN,
                STEP_APPROVE_PLAN,
                STEP_CONFIRM_ALL,
                STEP_WAIT_TERMINAL,
            ),
            expect={
                "run_status": "completed",
                "events_include": [
                    "run.created.v1",
                    "run.planned.v1",
                    "run.confirmation.required.v1",
                    "run.confirmation.received.v1",
                    "run.completed.v1",
                ],
                "events_exclude": ["run.failed.v1"],
                "tool_calls": ["external_publish"],
                "min_events": 5,
                "deliverables": 1,
            },
        ),
    )


def _tool_policy_cases() -> tuple[Case, ...]:
    return (
        Case(
            id="auto-policy-skips-confirmation",
            kind=KIND_TASK,
            goal="整理本周日程并发布说明",
            llm_script=(
                "# 执行计划\n- [ ] 检索日程\n- [ ] 发布说明",
                {
                    "tool_calls": [
                        {
                            "name": "external_publish",
                            "arguments": {"title": "周报"},
                        }
                    ]
                },
                "已发布周报。",
                "# 交付物\n\n周报已发布。",
            ),
            policies=({"tool": "external_publish", "action": "auto"},),
            expect={
                "run_status": "completed",
                "events_include": ["run.tool.called.v1", "run.completed.v1"],
                # 命中 auto 策略:不再出现确认闸事件。
                "events_exclude": [
                    "run.confirmation.required.v1",
                    "run.failed.v1",
                ],
                "tool_calls": ["external_publish"],
                "deliverables": 1,
            },
        ),
    )
