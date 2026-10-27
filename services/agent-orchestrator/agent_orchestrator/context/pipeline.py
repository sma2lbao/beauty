"""上下文装配管线:有序 section providers → 一个可渲染的 ContextPlan。

设计见 `docs/design/context-engineering.md` §6.2。管线本身不做 IO:

- AGENTS.md 发现(`context/agents.py`)、技能卡(`context/skills.py`)、
  记忆块(services 层已召回)各自实现 `SectionProvider`,由容器按开关注册;
- 任一 provider 失败只降级该段(reason 记进 skipped),绝不阻断 run。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

from agent_orchestrator.context.policy import SECTION_MEMORY, ContextPolicy


@dataclass(frozen=True, slots=True)
class ContextSection:
    """管线的一段上下文:有序、可渲染、可快照。"""

    id: str
    title: str
    content: str
    source: str = ""
    truncated: bool = False

    @property
    def chars(self) -> int:
        return len(self.content)

    def render(self) -> str:
        """渲染成系统提示里的一段(带二级标题);空段渲染为空串。"""

        if not self.content:
            return ""
        return f"\n\n## {self.title}\n\n{self.content}"

    def summary(self) -> dict[str, Any]:
        """事件快照用的描述(不含正文,避免事件体积爆炸)。"""

        return {
            "id": self.id,
            "title": self.title,
            "chars": self.chars,
            "source": self.source,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """管线的一次装配结果:按顺序的 sections + 未进上下文的 skipped 原因。"""

    policy: ContextPolicy
    sections: tuple[ContextSection, ...] = ()
    skipped: tuple[dict[str, str], ...] = ()

    @property
    def total_chars(self) -> int:
        return sum(section.chars for section in self.sections)

    @property
    def tokens_estimate(self) -> int:
        #: 与 `services/compaction.py` 同一口径:中文约 1 字/token,按 len//2 估。
        return self.total_chars // 2

    def render(self) -> str:
        """按 policy 顺序拼成系统提示块(空 plan 渲染为空串)。"""

        return "".join(section.render() for section in self.sections)

    def snapshot(self) -> dict[str, Any]:
        """`run.context.assembled.v1` 的 payload(策略 + 各段摘要 + 合计)。"""

        return {
            "policy": self.policy.to_dict(),
            "fingerprint": self.policy.fingerprint(),
            "sections": [section.summary() for section in self.sections],
            "skipped": [dict(item) for item in self.skipped],
            "total_chars": self.total_chars,
            "tokens_estimate": self.tokens_estimate,
        }


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """一次 run 的装配输入:只带值,不带客户端/会话(IO 由 provider 自己持有)。"""

    tenant_id: str
    run_id: str
    conversation_id: str | None = None
    workspace_id: str | None = None
    goal: str = ""
    title: str = ""
    skill_id: str | None = None
    #: run 创建时冻结的 SKILL.md 正文(渐进加载只在这里截卡片)
    skill_markdown: str | None = None
    #: services 层已召回的长期记忆块(自带小标题)
    memory_block: str = ""


@runtime_checkable
class SectionProvider(Protocol):
    """管线段提供者:按当前 run 的请求产出 0/1 段上下文。

    约定:IO 失败抛异常(管线降级为 skipped + reason);没有内容返回 None。
    """

    section_id: str

    async def build(self, request: ContextRequest) -> ContextSection | None: ...


@dataclass(frozen=True, slots=True)
class MemorySectionProvider:
    """已召回的长期记忆块 → memory 段(召回在 services 层,这里只做适配)。"""

    section_id: ClassVar[str] = SECTION_MEMORY

    async def build(self, request: ContextRequest) -> ContextSection | None:
        block = (request.memory_block or "").strip()
        if not block:
            return None
        return ContextSection(
            id=self.section_id,
            title="长期记忆(相关召回)",
            content=block,
            source="memory",
        )


class ContextPipeline:
    """可插拔上下文管线:注册表 + 有序装配。

    providers 是"段 id → 提供者"的注册表;`register` 覆盖同 id(容器装配期
    与测试期都能替换),`build` 只按 policy 顺序调用已注册的段。
    """

    def __init__(
        self,
        policy: ContextPolicy,
        *,
        providers: Sequence[SectionProvider] = (),
    ) -> None:
        self.policy = policy
        self.providers: dict[str, SectionProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: SectionProvider) -> None:
        """注册/覆盖一个段提供者。"""

        self.providers[str(provider.section_id)] = provider

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(self.providers)

    async def build(self, request: ContextRequest) -> ContextPlan:
        """按 policy 顺序装配;单段失败只降级该段。"""

        order = self.policy.normalized_order()
        if not self.policy.enabled:
            skipped = tuple(
                {"id": section_id, "reason": "policy_disabled"} for section_id in order
            )
            return ContextPlan(policy=self.policy, skipped=skipped)
        sections: list[ContextSection] = []
        skipped: list[dict[str, str]] = []
        for section_id in order:
            provider = self.providers.get(section_id)
            if provider is None:
                skipped.append({"id": section_id, "reason": "not_registered"})
                continue
            try:
                section = await provider.build(request)
            except Exception as exc:  # noqa: BLE001 - 单段失败不阻断 run
                skipped.append(
                    {"id": section_id, "reason": f"error:{type(exc).__name__}"}
                )
                continue
            if section is None or not section.content.strip():
                skipped.append({"id": section_id, "reason": "empty"})
                continue
            sections.append(section)
        return ContextPlan(
            policy=self.policy, sections=tuple(sections), skipped=tuple(skipped)
        )
