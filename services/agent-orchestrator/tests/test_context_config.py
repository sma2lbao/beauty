"""P1-F 上下文工程配置(§7.1):默认值与环境变量覆盖。

零网络:这里只构造 Settings,不起 app、不读本地 .env。pydantic-settings 的
env 源仍会读进程环境(Nx 会把项目 .env 注入任务环境,`_env_file=None` 挡不住),
所以 autouse fixture 先清掉 CONTEXT_* 覆盖,让"默认值"断言与机器环境无关。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from agent_orchestrator.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉环境里的 CONTEXT_* 覆盖(默认值断言只反映代码里的默认值)。"""
    for name in list(os.environ):
        if name.startswith("AGENT_ORCHESTRATOR__CONTEXT_"):
            monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> Settings:
    """自足配置:不读 .env,外部能力开关显式钉 False(本文件不建 app)。"""
    values: dict[str, Any] = {
        "llm_enabled": False,
        "tools_enabled": False,
        "knowledge_enabled": False,
        "outbox_enabled": False,
        "mcp_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_context_pipeline_defaults() -> None:
    """管线总开关默认开,分段顺序与设计 §7.1 一致。"""
    settings = _settings()
    assert settings.context_pipeline_enabled is True
    assert settings.context_section_order == ["agents_md", "skills", "memory"]


def test_agents_md_defaults() -> None:
    settings = _settings()
    assert settings.context_agents_md_enabled is True
    assert settings.context_agents_md_names == ["AGENTS.md"]
    assert settings.context_agents_md_max_chars == 4000
    assert settings.context_agents_md_file_max_chars == 2000
    assert settings.context_agents_md_max_depth == 2
    assert settings.context_agents_md_max_files == 8


def test_offload_defaults() -> None:
    settings = _settings()
    assert settings.context_offload_enabled is True
    assert settings.context_offload_threshold_chars == 8000
    assert settings.context_offload_preview_chars == 1200
    assert settings.context_offload_dir == ".agent/offload"
    assert settings.context_offload_max_chars == 200_000


def test_skill_and_sandbox_defaults() -> None:
    settings = _settings()
    assert settings.context_skill_loading_enabled is True
    assert settings.context_skill_card_max_chars == 400
    assert settings.context_skill_body_page_chars == 4000
    assert settings.context_sandbox_ttl_seconds == 900


def test_env_overrides_scalar_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 变量(AGENT_ORCHESTRATOR__ 前缀)优先于代码默认值。"""
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_PIPELINE_ENABLED", "false")
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_OFFLOAD_THRESHOLD_CHARS", "1234")
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_OFFLOAD_DIR", "tmp/offload")
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_SANDBOX_TTL_SECONDS", "60")
    settings = _settings()
    assert settings.context_pipeline_enabled is False
    assert settings.context_offload_threshold_chars == 1234
    assert settings.context_offload_dir == "tmp/offload"
    assert settings.context_sandbox_ttl_seconds == 60


def test_env_overrides_list_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """list 字段按 JSON 字符串从环境变量解析(pydantic-settings 口径)。"""
    monkeypatch.setenv(
        "AGENT_ORCHESTRATOR__CONTEXT_SECTION_ORDER", '["memory", "skills"]'
    )
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_AGENTS_MD_NAMES", '["CLAUDE.md"]')
    settings = _settings()
    assert settings.context_section_order == ["memory", "skills"]
    assert settings.context_agents_md_names == ["CLAUDE.md"]


def test_explicit_values_beat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式入参优先于 env(测试/装配处的自足口径)。"""
    monkeypatch.setenv("AGENT_ORCHESTRATOR__CONTEXT_OFFLOAD_MAX_CHARS", "1")
    settings = _settings(context_offload_max_chars=42)
    assert settings.context_offload_max_chars == 42
