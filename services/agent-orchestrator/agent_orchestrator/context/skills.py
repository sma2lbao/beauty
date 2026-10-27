"""SKILL.md 渐进加载:常驻卡片 + 分页正文工具(设计见 §6.5)。

技能正文不再全文塞进系统提示:管线只注入一张 ~100 token 的卡片
(`SkillCard`),模型需要完整步骤时调用 `load_skill(offset, limit)` 分页读取;
每次读取落 `run.skill.loaded.v1` 事件,审计能回答"哪个技能何时被读取"。

解析是朴素实现(零新依赖):支持 `---` frontmatter 的简单 `key: value`
(name/description 优先)、第一个 `# ` 标题与标题后的首个非空段落。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar

from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.pipeline import ContextRequest, ContextSection
from agent_orchestrator.context.policy import SECTION_SKILLS, ContextPolicy
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

#: load_skill 默认页大小(策略未配置/配置非法时的兜底)。
DEFAULT_BODY_PAGE_CHARS = 4000
#: 技能正文被读取的事件类型(§8)。
SKILL_LOADED_EVENT = "run.skill.loaded.v1"
#: 技能段在管线里的 id。
SKILL_SECTION_ID = SECTION_SKILLS

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class SkillCard:
    """一次 run 的技能元数据卡:常驻系统提示的只有它,正文按需分页读取。"""

    skill_id: str
    title: str
    #: frontmatter 的 description,否则正文标题后的首个段落。
    description: str
    #: 整个 SKILL.md 的字符数(与 load_skill 的 total_chars 同口径)。
    body_chars: int
    #: 注入系统提示的卡片文本(超策略上限时是完整卡片的截断前缀)。
    card: str


def _split_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """拆分 `---` 包围的 frontmatter(只认简单 `key: value`,解析失败原样返回)。

    值两侧的成对引号会去掉;没有闭合 `---` 时按"没有 frontmatter"处理,
    避免把正文里的分隔线误当元数据。
    """

    text = markdown.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        meta: dict[str, str] = {}
        for raw in lines[1:index]:
            key, separator, value = raw.partition(":")
            key = key.strip().lower()
            if not separator or not key:
                continue
            cleaned = value.strip().strip("\"'").strip()
            if cleaned:
                meta[key] = cleaned
        return meta, "\n".join(lines[index + 1 :])
    return {}, text


def _first_heading(body: str) -> tuple[int, str]:
    """第一个 `# ` 标题:(行号, 标题文本);没有标题返回 (-1, "")。"""

    for index, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if stripped.startswith("# "):
            return index, stripped[2:].strip()
    return -1, ""


def _first_paragraph(body: str, start_line: int) -> str:
    """标题之后的首个非空段落(连续非空行合并成一段,遇空行结束)。"""

    lines = body.splitlines()
    start = max(0, start_line + 1)
    collected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        collected.append(stripped)
    return " ".join(collected)


def _parse_skill(markdown: str) -> tuple[str, str, int]:
    """→(标题, 说明, 正文字符数);解析不出标题时由调用方回退 skill_id。"""

    meta, body = _split_frontmatter(markdown)
    heading_line, heading = _first_heading(body)
    title = heading or meta.get("name") or meta.get("title") or ""
    description = meta.get("description") or _first_paragraph(body, heading_line)
    return title, description, len(markdown)


def _compose_card(skill_id: str, title: str, description: str, body_chars: int) -> str:
    """固定格式的卡片文本(§6.5);超过策略上限时按前缀截断。"""

    return (
        f"技能:{title}(id: {skill_id})\n"
        f"说明:{description}\n"
        f"正文 {body_chars} 字;需要完整步骤时调用 load_skill(offset, limit) 分页读取。"
    )


def build_skill_card(
    skill_id: str,
    markdown: str,
    *,
    max_chars: int = 400,
) -> SkillCard:
    """解析 SKILL.md 元数据并生成常驻卡片。

    `max_chars` 是卡片长度上限(策略的 `skill_card_max_chars`):超出时截断,
    且保证截断结果仍是完整卡片的前缀(不加省略号,便于测试/比对)。
    """

    title, description, body_chars = _parse_skill(markdown)
    title = title or skill_id
    card = _compose_card(skill_id, title, description, body_chars)
    if max_chars > 0 and len(card) > max_chars:
        card = card[:max_chars]
    return SkillCard(
        skill_id=skill_id,
        title=title,
        description=description,
        body_chars=body_chars,
        card=card,
    )


def skill_body_page(
    markdown: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_BODY_PAGE_CHARS,
) -> tuple[str, int, bool]:
    """按字符分页读取技能正文 →(片段, 总字符数, 是否还有下一段)。

    边界:offset 负数按 0;limit ≤ 0 按默认页大小;offset 越界返回空串且
    `has_more=False`(不报错,模型可以据此停止翻页)。
    """

    text = markdown or ""
    total = len(text)
    start = offset if offset > 0 else 0
    if start >= total:
        return "", total, False
    size = limit if limit > 0 else DEFAULT_BODY_PAGE_CHARS
    end = min(start + size, total)
    return text[start:end], total, end < total


class SkillCardProvider:
    """skills 段:把 run 快照里的 SKILL.md 压成一张常驻卡片。

    开关关闭或本 run 没绑定正文时返回 None(管线记为 empty/跳过);正文读取
    由 `load_skill` 工具负责,provider 不做 IO。
    """

    section_id: ClassVar[str] = SKILL_SECTION_ID

    def __init__(self, policy: ContextPolicy) -> None:
        self.policy = policy

    async def build(self, request: ContextRequest) -> ContextSection | None:
        if not self.policy.skill_loading_enabled:
            return None
        markdown = request.skill_markdown or ""
        if not markdown.strip():
            return None
        skill_id = str(request.skill_id or "")
        card = build_skill_card(
            skill_id, markdown, max_chars=self.policy.skill_card_max_chars
        )
        # 用未截断卡片判断是否触发上限(截断后是前缀,长度比较最直接)。
        title, description, body_chars = _parse_skill(markdown)
        full_chars = len(
            _compose_card(skill_id, title or skill_id, description, body_chars)
        )
        return ContextSection(
            id=self.section_id,
            title="技能(SKILL.md 元数据)",
            content=card.card,
            source=f"skill:{skill_id}",
            truncated=full_chars > len(card.card),
        )


def _error(message: str) -> str:
    """工具错误统一回喂 JSON,模型能读懂并自行修正参数。"""

    return json.dumps({"error": message}, ensure_ascii=False)


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    """防御式取整参数:非 int(含 bool)或缺失时用默认值。"""

    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def build_load_skill_tool(
    session_factory: SessionFactory,
    *,
    policy: ContextPolicy,
) -> ToolSpec:
    """构造 `load_skill` 工具(READ):按 offset/limit 分页返回本 run 的技能正文。

    handler 从 `current_tool_context()` 拿租户/run,自建事务读取 run 快照;
    每次调用追加 `run.skill.loaded.v1` 事件(§8 审计口径)。
    """

    default_limit = policy.skill_body_page_chars or DEFAULT_BODY_PAGE_CHARS

    async def load_skill(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None or not context.run_id:
            return _error("缺少工具上下文(run),无法加载技能正文")
        offset = max(0, _int_arg(args, "offset", 0))
        limit = _int_arg(args, "limit", default_limit)
        if limit <= 0:
            limit = default_limit
        async with session_scope(session_factory) as session:
            run = await session.get(Run, context.run_id)
            if run is None or run.tenant_id != context.tenant_id:
                return _error("run 不存在或不属于当前租户")
            markdown = run.skill_system_prompt or ""
            if not markdown.strip():
                return _error("本 run 未绑定技能正文")
            page, total, has_more = skill_body_page(
                markdown, offset=offset, limit=limit
            )
            card = build_skill_card(
                str(run.skill_id or ""),
                markdown,
                max_chars=policy.skill_card_max_chars,
            )
            await append_event(
                session,
                run,
                SKILL_LOADED_EVENT,
                {
                    "skill_id": run.skill_id,
                    "offset": offset,
                    "limit": limit,
                    "chars": len(page),
                    "total_chars": total,
                    "truncated": has_more,
                },
            )
            payload = {
                "skill_id": run.skill_id,
                "title": card.title,
                "offset": offset,
                "limit": limit,
                "total_chars": total,
                "content": page,
                "truncated": has_more,
            }
        return json.dumps(payload, ensure_ascii=False)

    return ToolSpec(
        name="load_skill",
        description=(
            "分页读取本 run 技能 SKILL.md 的正文(只读)。先用 offset=0 读取开头;"
            "返回 truncated=true 时用 offset+limit 继续读下一页,读全后再按步骤执行。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "起始字符偏移,默认 0",
                },
                "limit": {
                    "type": "integer",
                    "default": default_limit,
                    "minimum": 1,
                    "description": f"本页字符数,默认 {default_limit}",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=load_skill,
        risk=ToolRisk.READ,
    )
