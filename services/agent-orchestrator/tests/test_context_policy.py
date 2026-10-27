"""上下文策略单测(§6.1):默认值、settings 投影、顺序归一、指纹与快照。

纯值对象,不碰数据库;settings 用 SimpleNamespace 替身,覆盖"全字段/缺字段/
空值回退"三种形态。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_orchestrator.context.policy import (
    DEFAULT_SECTION_ORDER,
    KNOWN_SECTIONS,
    SECTION_AGENTS_MD,
    SECTION_MEMORY,
    SECTION_SKILLS,
    CompactionPolicy,
    ContextPolicy,
    policy_from_settings,
)


def test_defaults_match_design_contract() -> None:
    policy = ContextPolicy()
    assert policy.enabled is True
    assert policy.section_order == (SECTION_AGENTS_MD, SECTION_SKILLS, SECTION_MEMORY)
    assert policy.section_order == DEFAULT_SECTION_ORDER == KNOWN_SECTIONS
    # 对话压缩默认开启,口径与 services/compaction.py 一致
    assert policy.compaction_enabled is True
    assert policy.compact_threshold_tokens == 6000
    assert policy.keep_recent_messages == 8
    assert policy.summary_max_chars == 2000
    # 技能渐进加载:卡片 ~400 字常驻,正文 4000 字一页
    assert policy.skill_loading_enabled is True
    assert policy.skill_card_max_chars == 400
    assert policy.skill_body_page_chars == 4000
    # 默认顺序是 tuple(可哈希,frozen dataclass 才能安全共享)
    assert isinstance(policy.section_order, tuple)


def test_policy_from_settings_reads_every_field() -> None:
    settings = SimpleNamespace(
        context_pipeline_enabled=False,
        context_section_order=["memory", "skills"],
        context_compaction_enabled=False,
        context_compact_threshold_tokens=1234,
        context_keep_recent_messages=3,
        context_summary_max_chars=99,
        context_agents_md_enabled=False,
        context_agents_md_names=["NOTES.md", "CONTRIBUTING.md"],
        context_agents_md_max_chars=111,
        context_agents_md_file_max_chars=22,
        context_agents_md_max_depth=0,
        context_agents_md_max_files=1,
        context_offload_enabled=False,
        context_offload_threshold_chars=100,
        context_offload_preview_chars=10,
        context_offload_dir="tmp/offload",
        context_offload_max_chars=1000,
        context_skill_loading_enabled=False,
        context_skill_card_max_chars=120,
        context_skill_body_page_chars=800,
    )
    assert policy_from_settings(settings) == ContextPolicy(
        enabled=False,
        section_order=("memory", "skills"),
        compaction_enabled=False,
        compact_threshold_tokens=1234,
        keep_recent_messages=3,
        summary_max_chars=99,
        agents_md_enabled=False,
        agents_md_names=("NOTES.md", "CONTRIBUTING.md"),
        agents_md_max_chars=111,
        agents_md_file_max_chars=22,
        agents_md_max_depth=0,
        agents_md_max_files=1,
        offload_enabled=False,
        offload_threshold_chars=100,
        offload_preview_chars=10,
        offload_dir="tmp/offload",
        offload_max_chars=1000,
        skill_loading_enabled=False,
        skill_card_max_chars=120,
        skill_body_page_chars=800,
    )


def test_policy_from_settings_falls_back_for_missing_fields() -> None:
    # 旧配置/测试替身没有 §7.1 新字段:一律回退 dataclass 默认
    assert policy_from_settings(SimpleNamespace()) == ContextPolicy()
    partial = policy_from_settings(SimpleNamespace(context_skill_body_page_chars=7))
    assert partial.skill_body_page_chars == 7
    assert partial.section_order == DEFAULT_SECTION_ORDER
    assert partial.skill_card_max_chars == 400


def test_policy_from_settings_empty_values_use_defaults() -> None:
    # 空列表/None 视为"未配置",不能变成空顺序(否则管线一段都装配不出来)
    settings = SimpleNamespace(
        context_section_order=[],
        context_agents_md_names=None,
    )
    policy = policy_from_settings(settings)
    assert policy.section_order == DEFAULT_SECTION_ORDER
    assert policy.agents_md_names == ("AGENTS.md",)


def test_normalized_order_drops_unknown_and_duplicates() -> None:
    policy = ContextPolicy().with_overrides(
        section_order=("memory", "nope", "memory", " skills ")
    )
    assert policy.normalized_order() == (SECTION_MEMORY, SECTION_SKILLS)


def test_normalized_order_coerces_non_string_entries() -> None:
    policy = ContextPolicy().with_overrides(section_order=(1, "skills"))  # type: ignore[arg-type]
    assert policy.normalized_order() == (SECTION_SKILLS,)


def test_normalized_order_falls_back_when_all_invalid() -> None:
    policy = ContextPolicy().with_overrides(section_order=("x", "y"))
    assert policy.normalized_order() == DEFAULT_SECTION_ORDER


def test_fingerprint_is_stable_across_equal_policies() -> None:
    first = ContextPolicy()
    second = ContextPolicy()
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 16
    # 16 位 hex(可哈希、可打印、事件里不占体积)
    int(first.fingerprint(), 16)


def test_fingerprint_changes_with_any_field() -> None:
    base = ContextPolicy()
    assert base.with_overrides(skill_card_max_chars=500).fingerprint() != (
        base.fingerprint()
    )
    assert base.with_overrides(section_order=("skills", "memory")).fingerprint() != (
        base.fingerprint()
    )
    # 归一后等价的顺序视为同一策略
    same = base.with_overrides(section_order=("agents_md", "skills", "memory"))
    assert same.fingerprint() == base.fingerprint()


def test_to_dict_is_json_serializable() -> None:
    policy = ContextPolicy()
    data = policy.to_dict()
    assert data["section_order"] == list(DEFAULT_SECTION_ORDER)
    assert data["agents_md_names"] == ["AGENTS.md"]
    assert isinstance(data["section_order"], list)
    encoded = json.dumps(data, ensure_ascii=False)
    assert "skills" in encoded


def test_compaction_projects_subset() -> None:
    policy = ContextPolicy().with_overrides(
        compaction_enabled=False,
        compact_threshold_tokens=1234,
        keep_recent_messages=3,
        summary_max_chars=99,
    )
    assert policy.compaction() == CompactionPolicy(
        enabled=False,
        threshold_tokens=1234,
        keep_recent_messages=3,
        summary_max_chars=99,
    )
    assert ContextPolicy().compaction() == CompactionPolicy()


def test_with_overrides_returns_new_policy() -> None:
    original = ContextPolicy()
    updated = original.with_overrides(enabled=False)
    assert updated is not original
    assert updated.enabled is False
    # 原策略不可变:同一 run 内快照稳定
    assert original.enabled is True
