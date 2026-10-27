"""容器上下文装配(设计 §7.2):策略快照、管线/offload 发布与图接线。

覆盖:

- policy 由 settings 构造一次,随 app.state 发布(开关/阈值透传);
- 沙箱就绪时管线注册 agents_md/skills/memory 三段,缺沙箱时退化为两段;
- context_offload_dir / context_sandbox_ttl_seconds 透传到 offloader 与文件层;
- extras 工厂按 policy 追加 load_skill / read_offloaded,开关全关时不装配 extras;
- build_run_graph 收到容器发布的 offloader(阶段一已支持该关键字)。

全部走内存假实现(FakeSandbox + monkeypatch),不连沙箱、不发网络。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import pytest
from common.clients.sandbox import SandboxExecResult, SandboxInfo
from fastapi.testclient import TestClient

from agent_orchestrator.context.offload import ToolOutputOffloader
from agent_orchestrator.context.pipeline import ContextPipeline, ContextRequest
from agent_orchestrator.context.policy import ContextPolicy
from agent_orchestrator.core import container as container_module
from agent_orchestrator.main import create_app
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _settings, verifier

#: 租户 id(conftest 的 TENANT 是鉴权头,不是 id)
TENANT_ID = "t-1"
AGENTS_MD = "# 工作区约定\n- 提交信息用中文"


@pytest.fixture(autouse=True)
def _clean_context_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉 Nx 注入的 AGENT_ORCHESTRATOR__CONTEXT_*:用例只吃显式 settings。"""

    for key in list(os.environ):
        if key.startswith("AGENT_ORCHESTRATOR__CONTEXT_"):
            monkeypatch.delenv(key, raising=False)


class FakeSandbox:
    """Sandbox 协议的假实现:内存文件表 + 固定 exec 结果(零网络)。"""

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        exec_stdout: str = "",
    ) -> None:
        self.files = dict(files or {})
        self.exec_stdout = exec_stdout
        self.ensure_calls: list[dict[str, object]] = []
        self.writes: list[tuple[str, str, str, bytes]] = []

    async def ensure(
        self,
        tenant_id: str,
        *,
        scope_key: str,
        kind: str = "generic",
        image: str | None = None,
        ttl_seconds: int | None = None,
        cpus: float | None = None,
        memory_mb: int | None = None,
        network: str | None = None,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[SandboxInfo, bool]:
        self.ensure_calls.append(
            {
                "tenant_id": tenant_id,
                "scope_key": scope_key,
                "ttl_seconds": ttl_seconds,
                "run_id": run_id,
            }
        )
        info = SandboxInfo(
            id=f"sbx-{len(self.ensure_calls)}", name=scope_key, status="running"
        )
        return info, True

    async def read_file(self, tenant_id: str, sandbox_id: str, path: str) -> bytes:
        if path not in self.files:
            raise RuntimeError(f"读取失败: {path}")
        return self.files[path]

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None:
        self.writes.append((tenant_id, sandbox_id, path, data))
        self.files[path] = data

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        return SandboxExecResult(
            exit_code=0,
            stdout=self.exec_stdout,
            stderr="",
            duration_ms=1,
            truncated=False,
        )


def _client(
    tmp_path: Any,
    name: str,
    *,
    sandbox: FakeSandbox | None = None,
    **overrides: Any,
) -> Iterator[TestClient]:
    """构造容器客户端:关 MCP,并显式打开上下文三个开关。

    conftest 的 _SETTINGS_DEFAULTS 为既有用例钉的是"上下文全关"的旧口径;
    这里显式覆盖成开启,不依赖默认值(用例可用 overrides 再改回关)。
    """

    overrides = {
        "mcp_enabled": False,
        "context_pipeline_enabled": True,
        "context_skill_loading_enabled": True,
        "context_offload_enabled": True,
        **overrides,
    }
    settings = _settings(tmp_path, name, **overrides)
    app = create_app(
        settings,
        sandbox_client=sandbox,
        token_verifier=verifier(),
    )
    return TestClient(app)


def _extras(state: Any, context: ToolContext) -> list[Any]:
    factory = state.tool_extras_factory
    assert factory is not None
    with bind_tool_context(context):
        return asyncio.run(factory(context.tenant_id))


@pytest.fixture()
def sandbox() -> FakeSandbox:
    return FakeSandbox(
        {"/workspace/AGENTS.md": AGENTS_MD.encode("utf-8")},
        exec_stdout="/workspace/AGENTS.md",
    )


def test_publishes_pipeline_with_all_sections_when_sandbox_ready(
    tmp_path: Any, sandbox: FakeSandbox
) -> None:
    with _client(tmp_path, "ctx-full.db", sandbox=sandbox) as client:
        state = client.app.state
        pipeline = state.context_pipeline
        assert isinstance(pipeline, ContextPipeline)
        # 段顺序即 policy.section_order:工作区约定 → 技能卡片 → 记忆
        assert pipeline.section_ids == ("agents_md", "skills", "memory")
        policy = state.context_policy
        assert isinstance(policy, ContextPolicy)
        assert policy.enabled is True
        assert policy.section_order == ("agents_md", "skills", "memory")

        # 同一份 pipeline 端到端可用:AGENTS.md 内容进渲染结果(不碰网络)
        plan = asyncio.run(
            pipeline.build(
                ContextRequest(tenant_id=TENANT_ID, run_id="r-1", conversation_id="c-1")
            )
        )
        assert "提交信息用中文" in plan.render()
        assert [section.id for section in plan.sections] == ["agents_md"]


def test_pipeline_without_sandbox_skips_agents_section(tmp_path: Any) -> None:
    with _client(tmp_path, "ctx-nosandbox.db") as client:
        state = client.app.state
        pipeline = state.context_pipeline
        assert isinstance(pipeline, ContextPipeline)
        assert pipeline.section_ids == ("skills", "memory")
        # 没有文件介质就无法落盘:offload 端口发布 None,节点退回原样回填
        assert state.tool_offloader is None


def test_publishes_nothing_when_pipeline_disabled(tmp_path: Any) -> None:
    with _client(
        tmp_path,
        "ctx-off.db",
        context_pipeline_enabled=False,
        context_offload_enabled=False,
    ) as client:
        state = client.app.state
        assert state.context_pipeline is None
        assert state.tool_offloader is None
        assert state.context_policy.enabled is False


def test_offloader_uses_settings_dir_ttl_and_policy(
    tmp_path: Any, sandbox: FakeSandbox
) -> None:
    with _client(
        tmp_path,
        "ctx-offload.db",
        sandbox=sandbox,
        context_offload_dir=".agent/custom",
        context_sandbox_ttl_seconds=123,
        context_offload_threshold_chars=42,
        context_skill_card_max_chars=77,
    ) as client:
        state = client.app.state
        offloader = state.tool_offloader
        assert isinstance(offloader, ToolOutputOffloader)
        assert offloader.prefix == "/workspace/.agent/custom"
        assert offloader.policy.offload_threshold_chars == 42
        # 文件层与策略同源:沙箱 TTL 来自配置,不是硬编码
        assert offloader.files.ttl_seconds == 123
        assert state.context_policy.skill_card_max_chars == 77


def test_offloader_none_when_offload_switch_off(
    tmp_path: Any, sandbox: FakeSandbox
) -> None:
    with _client(
        tmp_path, "ctx-offload-off.db", sandbox=sandbox, context_offload_enabled=False
    ) as client:
        state = client.app.state
        assert state.tool_offloader is None
        # 管线照旧发布(两个开关互不牵连)
        assert isinstance(state.context_pipeline, ContextPipeline)


def test_extras_exposes_load_skill_and_read_offloaded(
    tmp_path: Any, sandbox: FakeSandbox
) -> None:
    with _client(tmp_path, "ctx-extras.db", sandbox=sandbox) as client:
        specs = _extras(
            client.app.state, ToolContext(tenant_id=TENANT_ID, run_id="r-1")
        )
        names = [spec.name for spec in specs]
        assert names == ["load_skill", "read_offloaded"]
        assert all(spec.risk is ToolRisk.READ for spec in specs)


def test_extras_chat_path_has_no_run_only_tools(
    tmp_path: Any, sandbox: FakeSandbox
) -> None:
    """对话路径没有 run:load_skill/read_offloaded 永远不可用,不进工具面。"""

    with _client(tmp_path, "ctx-extras-chat.db", sandbox=sandbox) as client:
        specs = _extras(
            client.app.state,
            ToolContext(tenant_id=TENANT_ID, conversation_id="c-1"),
        )
        assert specs == []


def test_extras_without_sandbox_only_has_load_skill(tmp_path: Any) -> None:
    with _client(tmp_path, "ctx-extras-nosandbox.db") as client:
        specs = _extras(
            client.app.state, ToolContext(tenant_id=TENANT_ID, run_id="r-1")
        )
        assert [spec.name for spec in specs] == ["load_skill"]


def test_extras_not_assembled_when_context_switches_off(tmp_path: Any) -> None:
    with _client(
        tmp_path,
        "ctx-extras-off.db",
        context_skill_loading_enabled=False,
        context_offload_enabled=False,
    ) as client:
        # MCP/技能包/记忆/上下文全部关闭 → 不建 extras 工厂(零额外开销)
        assert client.app.state.tool_extras_factory is None


def test_build_run_graph_receives_offloader(
    tmp_path: Any, sandbox: FakeSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real = container_module.build_run_graph

    def spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(container_module, "build_run_graph", spy)
    with _client(tmp_path, "ctx-graph.db", sandbox=sandbox) as client:
        offloader = client.app.state.tool_offloader
        assert isinstance(offloader, ToolOutputOffloader)
        assert captured["offloader"] is offloader
        assert captured["mcp_tools_factory"] is client.app.state.tool_extras_factory
        assert client.app.state.run_graph is not None
