"""上下文工程策略:开关/预算/注入顺序的唯一真源(纯值对象)。

设计见 `docs/design/context-engineering.md` §6.1。要点:

- `ContextPolicy` 是 settings 的运行期投影,不是第二份配置:起图时由
  `policy_from_settings` 构造一次,快照进 `run.context.assembled.v1` 事件,
  同一 run 回放可复现(策略变化不影响已开始的 run)。
- `compaction()` 把压缩参数交给既有 `services/compaction.py`,本模块不实现压缩。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

#: 管线段 id(与 `ContextPipeline.section_order` 的值域一致)。
SECTION_AGENTS_MD = "agents_md"
SECTION_SKILLS = "skills"
SECTION_MEMORY = "memory"

#: 已知段顺序(默认注入顺序:AGENTS.md → 技能元数据 → 相关记忆)。
KNOWN_SECTIONS: tuple[str, ...] = (SECTION_AGENTS_MD, SECTION_SKILLS, SECTION_MEMORY)
DEFAULT_SECTION_ORDER = KNOWN_SECTIONS


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    """对话压缩参数(委托 `services/compaction.py` 执行)。"""

    enabled: bool = True
    threshold_tokens: int = 6000
    keep_recent_messages: int = 8
    summary_max_chars: int = 2000


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """一次 run 的上下文工程策略快照(字段与 settings 一一对应)。"""

    enabled: bool = True
    section_order: tuple[str, ...] = DEFAULT_SECTION_ORDER
    # 对话压缩(既有能力,参数进策略以便复现)
    compaction_enabled: bool = True
    compact_threshold_tokens: int = 6000
    keep_recent_messages: int = 8
    summary_max_chars: int = 2000
    # AGENTS.md 注入(沙箱 /workspace 内)
    agents_md_enabled: bool = True
    agents_md_names: tuple[str, ...] = ("AGENTS.md",)
    #: 所有 AGENTS.md 文件合计的注入预算(字符)
    agents_md_max_chars: int = 4000
    #: 单个文件最多注入多少字符
    agents_md_file_max_chars: int = 2000
    #: 相对 /workspace 的发现深度(0 = 只看根)
    agents_md_max_depth: int = 2
    agents_md_max_files: int = 8
    # 大工具结果 offload
    offload_enabled: bool = True
    #: 结果字符数超过该阈值才落盘
    offload_threshold_chars: int = 8000
    #: 引用信封里保留的预览字符数
    offload_preview_chars: int = 1200
    #: 工作区相对目录(绝对路径 = /workspace + 该目录)
    offload_dir: str = ".agent/offload"
    #: 落盘硬上限:超出部分截断并在信封里标注
    offload_max_chars: int = 200_000
    # SKILL.md 渐进加载
    skill_loading_enabled: bool = True
    #: 常驻的卡片字符上限(~100 token)
    skill_card_max_chars: int = 400
    #: load_skill 一次返回的正文页大小
    skill_body_page_chars: int = 4000

    def compaction(self) -> CompactionPolicy:
        """压缩策略子集(services 层压缩调用只认这个值对象)。"""

        return CompactionPolicy(
            enabled=self.compaction_enabled,
            threshold_tokens=self.compact_threshold_tokens,
            keep_recent_messages=self.keep_recent_messages,
            summary_max_chars=self.summary_max_chars,
        )

    def normalized_order(self) -> tuple[str, ...]:
        """去掉未知/重复段,保留声明顺序;过滤后为空则回退默认顺序。"""

        seen: set[str] = set()
        ordered: list[str] = []
        for section in self.section_order:
            name = str(section).strip()
            if name in KNOWN_SECTIONS and name not in seen:
                seen.add(name)
                ordered.append(name)
        return tuple(ordered) or DEFAULT_SECTION_ORDER

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化的事件快照(`run.context.assembled.v1` 用)。"""

        data = asdict(self)
        data["section_order"] = list(self.normalized_order())
        data["agents_md_names"] = list(self.agents_md_names)
        return data

    def fingerprint(self) -> str:
        """策略指纹:同一策略的稳定短哈希,便于比对/去重。"""

        payload = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def with_overrides(self, **changes: Any) -> ContextPolicy:
        """派生一个新策略(测试/场景覆盖用,不改原对象)。"""

        return replace(self, **changes)


def policy_from_settings(settings: Any) -> ContextPolicy:
    """settings → 运行期策略。

    用 `getattr` 而不是直接属性访问:测试替身与旧配置(无 §7.1 新字段)也要
    能构造出策略,缺失字段一律回退 dataclass 默认值。
    """

    order = getattr(settings, "context_section_order", None) or DEFAULT_SECTION_ORDER
    names = getattr(settings, "context_agents_md_names", None) or ("AGENTS.md",)
    return ContextPolicy(
        enabled=bool(getattr(settings, "context_pipeline_enabled", True)),
        section_order=tuple(str(item) for item in order),
        compaction_enabled=bool(getattr(settings, "context_compaction_enabled", True)),
        compact_threshold_tokens=int(
            getattr(settings, "context_compact_threshold_tokens", 6000)
        ),
        keep_recent_messages=int(getattr(settings, "context_keep_recent_messages", 8)),
        summary_max_chars=int(getattr(settings, "context_summary_max_chars", 2000)),
        agents_md_enabled=bool(getattr(settings, "context_agents_md_enabled", True)),
        agents_md_names=tuple(str(item) for item in names),
        agents_md_max_chars=int(getattr(settings, "context_agents_md_max_chars", 4000)),
        agents_md_file_max_chars=int(
            getattr(settings, "context_agents_md_file_max_chars", 2000)
        ),
        agents_md_max_depth=int(getattr(settings, "context_agents_md_max_depth", 2)),
        agents_md_max_files=int(getattr(settings, "context_agents_md_max_files", 8)),
        offload_enabled=bool(getattr(settings, "context_offload_enabled", True)),
        offload_threshold_chars=int(
            getattr(settings, "context_offload_threshold_chars", 8000)
        ),
        offload_preview_chars=int(
            getattr(settings, "context_offload_preview_chars", 1200)
        ),
        offload_dir=str(getattr(settings, "context_offload_dir", ".agent/offload")),
        offload_max_chars=int(getattr(settings, "context_offload_max_chars", 200_000)),
        skill_loading_enabled=bool(
            getattr(settings, "context_skill_loading_enabled", True)
        ),
        skill_card_max_chars=int(
            getattr(settings, "context_skill_card_max_chars", 400)
        ),
        skill_body_page_chars=int(
            getattr(settings, "context_skill_body_page_chars", 4000)
        ),
    )
