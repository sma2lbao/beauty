"""交付意图判定:只有用户明确要文件型成果时才落 .md 交付物。"""

from __future__ import annotations

import pytest

from agent_orchestrator.graph.deliver_nodes import wants_file_deliverable


@pytest.mark.parametrize(
    "goal",
    [
        "整理本周日程",
        "做一个贪吃蛇小游戏",
        "帮我排查登录接口 500",
        "总结一下这份会议纪要",
    ],
)
def test_plain_task_does_not_want_file(goal: str) -> None:
    assert wants_file_deliverable({"goal": goal}) is False


@pytest.mark.parametrize(
    "goal",
    [
        "生成一份 markdown 文件",
        "把结果导出成 .md",
        "写一份周报",
        "整理本周日程并输出文档",
        "导出 PDF 给我",
        "生成 word 版方案",
    ],
)
def test_file_request_wants_file(goal: str) -> None:
    assert wants_file_deliverable({"goal": goal}) is True


def test_extra_instructions_count() -> None:
    """追加指令里的诉求同样有效(它也是用户说的)。"""
    assert (
        wants_file_deliverable(
            {"goal": "整理本周日程", "extra_instructions": ["请给我 markdown"]}
        )
        is True
    )


def test_title_is_not_user_intent() -> None:
    """title 是模型概括,不能拿它决定要不要落文件。"""
    assert wants_file_deliverable({"goal": "整理本周日程", "title": "周报"}) is False
