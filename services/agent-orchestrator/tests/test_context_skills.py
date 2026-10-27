"""SKILL.md 渐进加载单测(§6.5):卡片提取、分页边界、provider 开关与工具。

工具 handler 直接调用(绕过 registry),临时 sqlite 文件库 + 真实 Run 行,
覆盖成功分页/审计事件/无上下文/无正文/租户不匹配分支;零网络。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.pipeline import ContextRequest
from agent_orchestrator.context.policy import ContextPolicy
from agent_orchestrator.context.skills import (
    DEFAULT_BODY_PAGE_CHARS,
    SKILL_LOADED_EVENT,
    SkillCard,
    SkillCardProvider,
    build_load_skill_tool,
    build_skill_card,
    skill_body_page,
)
from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _settings

TENANT = "t-1"
OTHER = "t-2"

SKILL_ID = "weekly-report"
SKILL_MD = (
    "---\n"
    "name: 周报助手\n"
    "description: 按模板生成中文周报\n"
    "---\n"
    "# 周报技能\n"
    "\n"
    "第一段说明:先收集本周进展,\n"
    "再按模板输出周报。\n"
    "\n"
    "## 步骤\n"
    "1. 收集进展\n"
)


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose。"""
    settings = _settings(tmp_path, "context-skills.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- 卡片提取 ----


def test_card_uses_frontmatter_description_and_heading() -> None:
    card = build_skill_card(SKILL_ID, SKILL_MD)
    assert isinstance(card, SkillCard)
    assert card.skill_id == SKILL_ID
    assert card.title == "周报技能"
    assert card.description == "按模板生成中文周报"
    assert card.body_chars == len(SKILL_MD)
    assert card.card == (
        f"技能:周报技能(id: {SKILL_ID})\n"
        "说明:按模板生成中文周报\n"
        f"正文 {len(SKILL_MD)} 字;"
        "需要完整步骤时调用 load_skill(offset, limit) 分页读取。"
    )


def test_card_title_falls_back_to_frontmatter_name() -> None:
    markdown = "---\nname: 周报助手\ndescription: 只是个说明\n---\n正文段落\n"
    card = build_skill_card(SKILL_ID, markdown)
    assert card.title == "周报助手"
    assert card.description == "只是个说明"


def test_card_description_falls_back_to_first_paragraph() -> None:
    markdown = "# 标题\n\n第一段\n继续说明\n\n第二段\n"
    card = build_skill_card(SKILL_ID, markdown)
    assert card.title == "标题"
    # 标题后的首个非空段落:连续非空行合并,遇空行结束
    assert card.description == "第一段 继续说明"


def test_card_falls_back_to_skill_id_without_heading() -> None:
    card = build_skill_card(SKILL_ID, "没有标题的正文\n\n第二段\n")
    assert card.title == SKILL_ID
    assert card.description == "没有标题的正文"


def test_card_keeps_quoted_frontmatter_values() -> None:
    markdown = "---\nname: \"周报助手\"\ndescription: '带引号说明'\n---\n# 标题\n"
    card = build_skill_card(SKILL_ID, markdown)
    assert card.title == "标题"
    assert card.description == "带引号说明"


def test_frontmatter_ignores_malformed_lines() -> None:
    # 缺分隔符/空键/空值都跳过,不抛错(技能包是外部输入)
    markdown = "---\n缺分隔符\n: 空键\nname: 周报助手\ndescription:   \n---\n# 标题\n"
    card = build_skill_card(SKILL_ID, markdown)
    assert card.title == "标题"
    assert card.description == ""


def test_unclosed_frontmatter_is_not_parsed() -> None:
    # 正文里的分隔线不能被误当元数据:找不到闭合 --- 时整段按正文处理
    markdown = "---\nname: 不该生效\n# 真标题\n\n正文段落\n"
    card = build_skill_card(SKILL_ID, markdown)
    assert card.title == "真标题"
    assert card.description == "正文段落"
    assert card.body_chars == len(markdown)


def test_truncation_keeps_card_as_prefix() -> None:
    full = build_skill_card(SKILL_ID, SKILL_MD, max_chars=0)
    small = build_skill_card(SKILL_ID, SKILL_MD, max_chars=20)
    assert len(small.card) == 20
    assert small.card == full.card[:20]
    # 元数据不受截断影响
    assert (small.title, small.description) == (full.title, full.description)


def test_default_card_limit_is_400_chars() -> None:
    markdown = "# 标题\n\n" + "很长的说明" * 100 + "\n"
    full = build_skill_card(SKILL_ID, markdown, max_chars=0)
    card = build_skill_card(SKILL_ID, markdown)
    assert len(card.card) == 400
    assert card.card == full.card[:400]


# ---- 分页边界 ----


def test_page_returns_first_slice() -> None:
    page, total, has_more = skill_body_page("abcdefghij", offset=0, limit=4)
    assert (page, total, has_more) == ("abcd", 10, True)


def test_page_middle_and_last_slices() -> None:
    assert skill_body_page("abcdefghij", offset=4, limit=4) == ("efgh", 10, True)
    assert skill_body_page("abcdefghij", offset=8, limit=4) == ("ij", 10, False)


def test_page_offset_at_or_beyond_total_is_empty() -> None:
    assert skill_body_page("abcdefghij", offset=10, limit=4) == ("", 10, False)
    assert skill_body_page("abcdefghij", offset=99, limit=4) == ("", 10, False)


def test_page_negative_offset_is_clamped_to_zero() -> None:
    assert skill_body_page("abcdefghij", offset=-3, limit=4) == ("abcd", 10, True)


def test_page_non_positive_limit_uses_default_page_size() -> None:
    markdown = "字" * 5000
    assert skill_body_page(markdown, offset=0, limit=0) == (
        markdown[:DEFAULT_BODY_PAGE_CHARS],
        5000,
        True,
    )
    assert skill_body_page(markdown, offset=0, limit=-1) == (
        markdown[:DEFAULT_BODY_PAGE_CHARS],
        5000,
        True,
    )


def test_page_default_limit_returns_whole_short_body() -> None:
    assert skill_body_page("abcdefghij") == ("abcdefghij", 10, False)


def test_page_empty_markdown() -> None:
    assert skill_body_page("") == ("", 0, False)


# ---- SkillCardProvider ----


def _request(markdown: str | None = SKILL_MD, skill_id: str | None = SKILL_ID):
    return ContextRequest(
        tenant_id=TENANT,
        run_id="run-1",
        skill_id=skill_id,
        skill_markdown=markdown,
    )


async def test_provider_returns_none_when_disabled() -> None:
    policy = ContextPolicy().with_overrides(skill_loading_enabled=False)
    assert await SkillCardProvider(policy).build(_request()) is None


async def test_provider_returns_none_without_markdown() -> None:
    provider = SkillCardProvider(ContextPolicy())
    for markdown in (None, "", "   \n  "):
        assert await provider.build(_request(markdown)) is None


async def test_provider_builds_skills_section_from_card() -> None:
    provider = SkillCardProvider(ContextPolicy())
    section = await provider.build(_request())
    assert section is not None
    assert section.id == "skills"
    assert section.title == "技能(SKILL.md 元数据)"
    assert section.source == f"skill:{SKILL_ID}"
    assert section.content == build_skill_card(SKILL_ID, SKILL_MD).card
    assert section.truncated is False


async def test_provider_flags_truncated_card() -> None:
    policy = ContextPolicy().with_overrides(skill_card_max_chars=12)
    section = await SkillCardProvider(policy).build(_request())
    assert section is not None
    assert len(section.content) == 12
    assert section.truncated is True


async def test_provider_without_skill_id_uses_empty_source() -> None:
    section = await SkillCardProvider(ContextPolicy()).build(_request(skill_id=None))
    assert section is not None
    assert section.source == "skill:"


# ---- load_skill 工具 ----


def _tool(
    factory: async_sessionmaker[AsyncSession],
    *,
    page: int = 64,
):
    policy = ContextPolicy().with_overrides(skill_body_page_chars=page)
    return build_load_skill_tool(factory, policy=policy)


async def _create_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT,
    skill_id: str | None = SKILL_ID,
    markdown: str | None = SKILL_MD,
) -> str:
    async with session_scope(factory) as session:
        run = Run(
            tenant_id=tenant_id,
            title="周报",
            goal="整理本周日程",
            skill_id=skill_id,
            skill_system_prompt=markdown,
        )
        session.add(run)
        await session.flush()
        return run.id


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunEvent]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
        return list(rows.all())


async def test_tool_spec_declares_read_risk_and_paging_schema(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory, page=64)
    assert tool.name == "load_skill"
    assert tool.risk is ToolRisk.READ
    assert "SKILL.md" in tool.description
    schema = tool.parameters
    assert schema["type"] == "object"
    assert schema["required"] == []
    assert schema["additionalProperties"] is False
    assert schema["properties"]["offset"]["default"] == 0
    assert schema["properties"]["limit"]["default"] == 64


async def test_tool_reads_first_page_and_records_event(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory, page=10)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        raw = await tool.handler({"offset": 0, "limit": 10})
        payload = json.loads(raw)
    # 中文不转义(模型与日志都要可读)
    assert "周报" in raw
    assert payload["skill_id"] == SKILL_ID
    assert payload["title"] == "周报技能"
    assert payload["offset"] == 0
    assert payload["limit"] == 10
    assert payload["content"] == SKILL_MD[:10]
    assert payload["total_chars"] == len(SKILL_MD)
    assert payload["truncated"] is True

    events = await _events(factory, run_id)
    assert [event.type for event in events] == [SKILL_LOADED_EVENT]
    assert events[0].seq == 1
    assert events[0].tenant_id == TENANT
    assert events[0].payload == {
        "skill_id": SKILL_ID,
        "offset": 0,
        "limit": 10,
        "chars": 10,
        "total_chars": len(SKILL_MD),
        "truncated": True,
    }


async def test_tool_pages_forward_and_appends_one_event_per_call(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory, page=10)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        first = json.loads(await tool.handler({"offset": 0, "limit": 10}))
        second = json.loads(await tool.handler({"offset": 10, "limit": 10}))
    assert first["content"] == SKILL_MD[:10]
    assert second["content"] == SKILL_MD[10:20]
    assert second["truncated"] == (len(SKILL_MD) > 20)
    events = await _events(factory, run_id)
    assert [event.seq for event in events] == [1, 2]
    assert [event.payload["offset"] for event in events] == [0, 10]


async def test_tool_defaults_and_defensive_arguments(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory, page=64)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        defaulted = json.loads(await tool.handler({}))
        clamped = json.loads(await tool.handler({"offset": -5, "limit": "bad"}))
        bool_limit = json.loads(await tool.handler({"offset": "x", "limit": True}))
        zero_limit = json.loads(await tool.handler({"offset": 0, "limit": 0}))
        beyond = json.loads(await tool.handler({"offset": len(SKILL_MD) + 100}))
    assert defaulted["offset"] == 0
    assert defaulted["limit"] == 64
    assert defaulted["content"] == SKILL_MD[:64]
    # 非 int / bool 参数一律回退默认;负 offset 夹到 0
    assert (clamped["offset"], clamped["limit"]) == (0, 64)
    assert (bool_limit["offset"], bool_limit["limit"]) == (0, 64)
    assert (zero_limit["limit"], zero_limit["content"]) == (64, SKILL_MD[:64])
    assert beyond["content"] == ""
    assert beyond["truncated"] is False


async def test_tool_default_page_follows_policy(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = build_load_skill_tool(factory, policy=ContextPolicy())
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        payload = json.loads(await tool.handler({}))
    assert payload["limit"] == 4000
    assert payload["content"] == SKILL_MD
    assert payload["truncated"] is False


async def test_tool_falls_back_to_skill_id_as_title(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory, markdown="没有标题的正文\n")
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        payload = json.loads(await tool.handler({}))
    assert payload["title"] == SKILL_ID


async def test_tool_requires_run_context(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    # 完全没有工具上下文
    payload = json.loads(await tool.handler({}))
    assert "缺少工具上下文" in payload["error"]
    # 有租户但没有 run(对话/只读路径)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        payload = json.loads(await tool.handler({}))
    assert "缺少工具上下文" in payload["error"]
    assert await _events(factory, run_id) == []


async def test_tool_rejects_other_tenant(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=OTHER, run_id=run_id)):
        payload = json.loads(await tool.handler({}))
    assert "run 不存在或不属于当前租户" in payload["error"]
    assert await _events(factory, run_id) == []


async def test_tool_reports_missing_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=uuid4().hex)):
        payload = json.loads(await tool.handler({}))
    assert "run 不存在或不属于当前租户" in payload["error"]


async def test_tool_reports_run_without_skill_body(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    absent = await _create_run(factory, markdown=None)
    blank = await _create_run(factory, markdown="   \n  ")
    for run_id in (absent, blank):
        with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
            payload = json.loads(await tool.handler({}))
        assert "本 run 未绑定技能正文" in payload["error"]
        assert await _events(factory, run_id) == []
