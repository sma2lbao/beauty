"""上下文装配管线单测(§6.2):顺序、覆盖、降级与快照。

管线本身零 IO:用 FakeSectionProvider 覆盖"正常/空/抛异常/未注册"四种情况,
MemorySectionProvider 只做记忆块适配。
"""

from __future__ import annotations

from typing import Any

from agent_orchestrator.context.pipeline import (
    ContextPipeline,
    ContextPlan,
    ContextRequest,
    ContextSection,
    MemorySectionProvider,
    SectionProvider,
)
from agent_orchestrator.context.policy import (
    DEFAULT_SECTION_ORDER,
    SECTION_AGENTS_MD,
    SECTION_MEMORY,
    SECTION_SKILLS,
    ContextPolicy,
)


class FakeProvider:
    """记录调用、可返回 None/空内容/抛异常的段提供者(零 IO)。"""

    def __init__(
        self,
        section_id: str,
        content: str | None = "段内容",
        *,
        error: Exception | None = None,
    ) -> None:
        self.section_id = section_id
        self._content = content
        self._error = error
        self.requests: list[ContextRequest] = []

    async def build(self, request: ContextRequest) -> ContextSection | None:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._content is None:
            return None
        return ContextSection(
            id=self.section_id,
            title=f"{self.section_id} 标题",
            content=self._content,
            source="fake",
        )


def _request(**overrides: Any) -> ContextRequest:
    return ContextRequest(tenant_id="t-1", run_id="run-1", **overrides)


async def test_build_returns_sections_in_policy_order() -> None:
    memory = FakeProvider(SECTION_MEMORY, "记忆块")
    agents = FakeProvider(SECTION_AGENTS_MD, "AGENTS.md 内容")
    # 注册顺序与注入顺序无关:按 policy.normalized_order() 装配
    pipeline = ContextPipeline(ContextPolicy(), providers=[memory, agents])
    plan = await pipeline.build(_request())
    assert [section.id for section in plan.sections] == [
        SECTION_AGENTS_MD,
        SECTION_MEMORY,
    ]
    # skills 没注册:降级为 skipped,不影响其它段
    assert plan.skipped == ({"id": SECTION_SKILLS, "reason": "not_registered"},)


async def test_build_uses_policy_section_order() -> None:
    agents = FakeProvider(SECTION_AGENTS_MD, "AGENTS.md 内容")
    memory = FakeProvider(SECTION_MEMORY, "记忆块")
    policy = ContextPolicy().with_overrides(
        section_order=(SECTION_MEMORY, SECTION_AGENTS_MD)
    )
    pipeline = ContextPipeline(policy, providers=[agents, memory])
    plan = await pipeline.build(_request())
    assert [section.id for section in plan.sections] == [
        SECTION_MEMORY,
        SECTION_AGENTS_MD,
    ]
    # 顺序里没有 skills,自然也不会记 skipped
    assert plan.skipped == ()


def test_register_overrides_same_section_id() -> None:
    first = FakeProvider(SECTION_MEMORY, "旧实现")
    second = FakeProvider(SECTION_MEMORY, "新实现")
    pipeline = ContextPipeline(ContextPolicy(), providers=[first])
    pipeline.register(second)
    assert pipeline.section_ids == (SECTION_MEMORY,)
    assert pipeline.providers[SECTION_MEMORY] is second


async def test_register_override_takes_effect_on_build() -> None:
    policy = ContextPolicy().with_overrides(section_order=(SECTION_MEMORY,))
    pipeline = ContextPipeline(policy, providers=[FakeProvider(SECTION_MEMORY, "旧")])
    pipeline.register(FakeProvider(SECTION_MEMORY, "新"))
    plan = await pipeline.build(_request())
    assert [section.content for section in plan.sections] == ["新"]


async def test_unregistered_section_is_skipped() -> None:
    policy = ContextPolicy().with_overrides(section_order=(SECTION_SKILLS,))
    plan = await ContextPipeline(policy).build(_request())
    assert plan.sections == ()
    assert plan.skipped == ({"id": SECTION_SKILLS, "reason": "not_registered"},)


async def test_empty_and_none_outputs_are_skipped() -> None:
    policy = ContextPolicy().with_overrides(
        section_order=(SECTION_AGENTS_MD, SECTION_MEMORY, SECTION_SKILLS)
    )
    pipeline = ContextPipeline(
        policy,
        providers=[
            FakeProvider(SECTION_AGENTS_MD, "  "),
            FakeProvider(SECTION_MEMORY, None),
            FakeProvider(SECTION_SKILLS, "可用内容"),
        ],
    )
    plan = await pipeline.build(_request())
    assert [section.id for section in plan.sections] == [SECTION_SKILLS]
    assert plan.skipped == (
        {"id": SECTION_AGENTS_MD, "reason": "empty"},
        {"id": SECTION_MEMORY, "reason": "empty"},
    )


async def test_provider_failure_only_degrades_that_section() -> None:
    pipeline = ContextPipeline(
        ContextPolicy(),
        providers=[
            FakeProvider(SECTION_AGENTS_MD, error=RuntimeError("沙箱不可达")),
            FakeProvider(SECTION_SKILLS, "技能卡"),
            FakeProvider(SECTION_MEMORY, "记忆块"),
        ],
    )
    plan = await pipeline.build(_request())
    assert [section.id for section in plan.sections] == [
        SECTION_SKILLS,
        SECTION_MEMORY,
    ]
    assert plan.skipped == ({"id": SECTION_AGENTS_MD, "reason": "error:RuntimeError"},)


async def test_policy_disabled_skips_everything_without_calling_providers() -> None:
    agents = FakeProvider(SECTION_AGENTS_MD, "AGENTS.md 内容")
    memory = FakeProvider(SECTION_MEMORY, "记忆块")
    policy = ContextPolicy().with_overrides(enabled=False)
    pipeline = ContextPipeline(policy, providers=[agents, memory])
    plan = await pipeline.build(_request())
    assert plan.sections == ()
    assert plan.skipped == tuple(
        {"id": section_id, "reason": "policy_disabled"}
        for section_id in DEFAULT_SECTION_ORDER
    )
    # 关闭时不碰 provider(不产生沙箱/数据库 IO)
    assert agents.requests == []
    assert memory.requests == []


async def test_request_is_passed_through_unchanged() -> None:
    provider = FakeProvider(SECTION_MEMORY, "记忆块")
    pipeline = ContextPipeline(
        ContextPolicy().with_overrides(section_order=(SECTION_MEMORY,)),
        providers=[provider],
    )
    request = _request(workspace_id="ws-1", goal="整理日程", memory_block="记忆块")
    await pipeline.build(request)
    assert provider.requests == [request]


async def test_render_joins_sections_with_markdown_headings() -> None:
    pipeline = ContextPipeline(
        ContextPolicy(),
        providers=[
            FakeProvider(SECTION_AGENTS_MD, "规则"),
            FakeProvider(SECTION_SKILLS, "卡片"),
            FakeProvider(SECTION_MEMORY, "记忆"),
        ],
    )
    plan = await pipeline.build(_request())
    assert plan.render() == (
        "\n\n## agents_md 标题\n\n规则"
        "\n\n## skills 标题\n\n卡片"
        "\n\n## memory 标题\n\n记忆"
    )


async def test_empty_plan_renders_empty_string() -> None:
    plan = await ContextPipeline(ContextPolicy().with_overrides(enabled=False)).build(
        _request()
    )
    assert isinstance(plan, ContextPlan)
    assert plan.render() == ""
    assert plan.total_chars == 0
    assert plan.tokens_estimate == 0


async def test_snapshot_carries_policy_fingerprint_and_totals() -> None:
    policy = ContextPolicy()
    pipeline = ContextPipeline(
        policy,
        providers=[
            FakeProvider(SECTION_AGENTS_MD, "规则"),
            FakeProvider(SECTION_SKILLS, "卡片"),
        ],
    )
    plan = await pipeline.build(_request())
    snapshot = plan.snapshot()
    assert set(snapshot) == {
        "policy",
        "fingerprint",
        "sections",
        "skipped",
        "total_chars",
        "tokens_estimate",
    }
    assert snapshot["policy"] == policy.to_dict()
    assert snapshot["fingerprint"] == policy.fingerprint()
    assert [section["id"] for section in snapshot["sections"]] == [
        SECTION_AGENTS_MD,
        SECTION_SKILLS,
    ]
    assert snapshot["sections"][0] == {
        "id": SECTION_AGENTS_MD,
        "title": "agents_md 标题",
        "chars": 2,
        "source": "fake",
        "truncated": False,
    }
    assert snapshot["skipped"] == [{"id": SECTION_MEMORY, "reason": "not_registered"}]
    assert snapshot["total_chars"] == 4
    assert snapshot["tokens_estimate"] == 2


def test_context_section_render_and_chars() -> None:
    section = ContextSection(id="memory", title="记忆", content="事实", source="memory")
    assert section.chars == 2
    assert section.render() == "\n\n## 记忆\n\n事实"
    empty = ContextSection(id="memory", title="记忆", content="")
    assert empty.chars == 0
    assert empty.render() == ""


def test_section_provider_protocol_is_runtime_checkable() -> None:
    assert isinstance(MemorySectionProvider(), SectionProvider)
    assert isinstance(FakeProvider(SECTION_MEMORY), SectionProvider)


async def test_memory_provider_returns_none_without_block() -> None:
    provider = MemorySectionProvider()
    assert await provider.build(_request()) is None
    assert await provider.build(_request(memory_block="   \n  ")) is None


async def test_memory_provider_maps_block_to_section() -> None:
    provider = MemorySectionProvider()
    section = await provider.build(_request(memory_block="  用户偏好中文周报  "))
    assert section is not None
    assert section.id == SECTION_MEMORY
    assert section.title == "长期记忆(相关召回)"
    assert section.content == "用户偏好中文周报"
    assert section.source == "memory"
    assert section.truncated is False
