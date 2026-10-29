"""ask_question:交互工具在 tool-service 侧只校验与归一、不执行(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.services.execution import (
    audit_columns,
)

# ---- ask_question:交互工具在 tool-service 侧只校验+归一,不执行 ----------


async def test_ask_question_returns_interaction_result(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)
    context = execution_context()
    arguments = {
        "questions": [
            {
                "question": "选择目标环境",
                "header": "环境",
                "options": [
                    {"label": "staging", "description": "预发环境"},
                    {"label": "prod"},
                ],
                "multi_select": True,
            },
            {
                "question": "补充说明?",
                "options": [{"label": "无"}, {"label": "见备注"}],
            },
        ],
        "allow_custom": False,
    }

    outcome = await runner.execute(context, "ask_question", arguments)

    assert outcome.response.ok is True
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    result = outcome.response.result
    assert result is not None
    assert result["kind"] == "interaction"
    assert result["interaction"] == {"type": "question"}
    normalized = result["normalized"]
    assert normalized["allow_custom"] is False
    assert normalized["questions"] == [
        {
            "question": "选择目标环境",
            "header": "环境",
            "options": [
                {"label": "staging", "description": "预发环境"},
                {"label": "prod"},
            ],
            "multi_select": True,
        },
        {
            "question": "补充说明?",
            "options": [{"label": "无"}, {"label": "见备注"}],
            "multi_select": False,
        },
    ]
    # 交互工具不进沙箱:零 ensure/零 exec。
    assert provider.ensured == []
    assert provider.exec_calls == []
    assert outcome.sandbox_id is None
    columns = audit_columns(outcome, context, arguments, decision="auto")
    assert columns["tool_name"] == "ask_question"
    assert columns["risk"] == "read"
    assert columns["ok"] is True
    assert columns["bytes_written"] == 0
    assert json.loads(columns["paths"]) == []


async def test_ask_question_fills_defaults(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(
        execution_context(),
        "ask_question",
        {"questions": [{"question": "q", "options": [{"label": "a"}, {"label": "b"}]}]},
    )

    normalized = outcome.response.result["normalized"]  # type: ignore[index]
    assert normalized["allow_custom"] is True
    assert normalized["questions"] == [
        {
            "question": "q",
            "options": [{"label": "a"}, {"label": "b"}],
            "multi_select": False,
        }
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"questions": []},
        {
            "questions": [
                {"question": "q", "options": [{"label": "a"}, {"label": "b"}]}
            ]
            * 5
        },
        {"questions": [{"question": "q", "options": []}]},
        {"questions": [{"question": "q"}]},
        {
            "questions": [
                {"question": "q", "options": [{"label": "a"}, {"label": "b"}]}
            ],
            "extra": True,
        },
        {"questions": [{"question": "", "options": [{"label": "a"}, {"label": "b"}]}]},
    ],
)
async def test_ask_question_rejects_invalid_arguments(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "ask_question", arguments)

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.ensured == []
    assert provider.exec_calls == []
