"""记忆注入与工具装配(§6.7):节点提示词、服务层召回降级、工具面开关。

覆盖三件事,都按"外层可观测行为"断言而不是内部调用细节:
1. 四个节点(plan/execute/verify/deliver)的系统提示词只在有召回块时追加,
   格式与技能约束/追加指令一致(空串=完全不加,提示词逐字不变);
2. services 层召回:带租户/工作区/限额调用,任何异常都降级空串不阻断 run,
   且开关关闭时根本不查记忆;
3. 工具装配:memory_enabled 真才把记忆工具并入 extras(能力即权限边界)。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.container_assemblies import ContainerAssemblyMixin
from agent_orchestrator.graph.deliver_nodes import (
    DELIVER_SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
    make_deliver_node,
    make_verify_node,
)
from agent_orchestrator.graph.nodes import (
    EXECUTE_SYSTEM_PROMPT,
    _execute_messages,
)
from agent_orchestrator.graph.plan_nodes import (
    PLAN_SYSTEM_PROMPT,
    _memory_block,
    make_plan_node,
)
from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import run_execution as run_execution_module
from agent_orchestrator.services.run_execution import (
    _memory_query,
    _recall_memory_block,
)
from agent_orchestrator.services.run_execution_approvals import _tool_context
from agent_orchestrator.services.tool_context import ToolContext, current_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import TENANT, FakeLLM, _create_run, _settings, verifier

#: 召回块由 services 层整体拼好(自带小标题),节点只负责前置空行。
MEMORY_BLOCK = "## 长期记忆(跨会话,仅供参考)\n- 用户偏好:周报用表格"


def _state(**overrides: Any) -> dict[str, Any]:
    """最小可跑状态:节点只读这些键(其余缺省按空处理)。"""
    state: dict[str, Any] = {
        "run_id": "run-1",
        "tenant_id": "t-1",
        "title": "周报",
        "goal": "整理本周日程",
        "acceptance_criteria": ["日程已整理"],
    }
    state.update(overrides)
    return state


def _memory_settings(**overrides: Any) -> Settings:
    """自足配置:不读 .env,显式钉住所有外部开关(零网络)。"""
    values: dict[str, Any] = {
        "llm_enabled": False,
        "tools_enabled": False,
        "knowledge_enabled": False,
        "outbox_enabled": False,
        "mcp_enabled": False,
        "memory_enabled": True,
        "memory_recall_limit": 7,
        "memory_recall_max_chars": 1234,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _run(**overrides: Any) -> Run:
    """未落的 Run 行:够拼召回查询词与租户/工作区上下文即可。"""
    values: dict[str, Any] = {
        "tenant_id": "t-1",
        "title": "周报",
        "goal": "整理本周日程",
    }
    values.update(overrides)
    return Run(**values)


def test_memory_block_is_prefixed_and_empty_when_absent() -> None:
    """空块不加任何字符(含换行);有块则前置空行与技能约束同形。"""
    assert _memory_block(_state()) == ""
    assert _memory_block(_state(memory_block="")) == ""
    assert _memory_block(_state(memory_block=MEMORY_BLOCK)) == f"\n\n{MEMORY_BLOCK}"


async def test_plan_node_appends_memory_block() -> None:
    fake = FakeLLM(["# 计划"])
    await make_plan_node(fake)(_state(memory_block=MEMORY_BLOCK))
    assert fake.calls[0][0]["content"] == PLAN_SYSTEM_PROMPT + f"\n\n{MEMORY_BLOCK}"


async def test_plan_node_prompt_unchanged_without_memory() -> None:
    fake = FakeLLM(["# 计划"])
    await make_plan_node(fake)(_state())
    assert fake.calls[0][0]["content"] == PLAN_SYSTEM_PROMPT


def test_execute_messages_append_memory_block() -> None:
    messages = _execute_messages(_state(memory_block=MEMORY_BLOCK))
    assert messages[0]["content"] == EXECUTE_SYSTEM_PROMPT + f"\n\n{MEMORY_BLOCK}"
    assert _execute_messages(_state())[0]["content"] == EXECUTE_SYSTEM_PROMPT


async def test_verify_node_appends_memory_block() -> None:
    verdicts = '{"summary": "ok", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM([verdicts])
    await make_verify_node(fake)(_state(memory_block=MEMORY_BLOCK))
    assert fake.calls[0][0]["content"] == VERIFY_SYSTEM_PROMPT + f"\n\n{MEMORY_BLOCK}"


async def test_verify_node_prompt_unchanged_without_memory() -> None:
    verdicts = '{"summary": "ok", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM([verdicts])
    await make_verify_node(fake)(_state())
    assert fake.calls[0][0]["content"] == VERIFY_SYSTEM_PROMPT


async def test_deliver_node_appends_memory_block() -> None:
    fake = FakeLLM(["# 交付物"])
    await make_deliver_node(fake)(_state(memory_block=MEMORY_BLOCK))
    assert fake.calls[0][0]["content"] == DELIVER_SYSTEM_PROMPT + f"\n\n{MEMORY_BLOCK}"


async def test_deliver_node_prompt_unchanged_without_memory() -> None:
    fake = FakeLLM(["# 交付物"])
    await make_deliver_node(fake)(_state())
    assert fake.calls[0][0]["content"] == DELIVER_SYSTEM_PROMPT


def test_memory_query_joins_goal_and_title() -> None:
    assert _memory_query(_run()) == "整理本周日程 周报"
    assert _memory_query(_run(goal="", title="周报")) == "周报"
    assert _memory_query(_run(goal="", title="")) == ""


async def test_recall_memory_block_passes_scope_and_limits(monkeypatch) -> None:
    """召回按契约传租户/工作区/限额;查询词以目标为主、标题为辅。"""
    calls: list[dict[str, Any]] = []

    async def fake_convention(*args: Any, **kwargs: Any) -> str:
        return ""

    async def fake_recall(session, tenant_id, query, **kwargs):
        calls.append(
            {
                "session": session,
                "tenant_id": tenant_id,
                "query": query,
                **kwargs,
            }
        )
        return f"\n\n{MEMORY_BLOCK}"

    monkeypatch.setattr(
        run_execution_module.memory_service, "convention_block", fake_convention
    )
    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", fake_recall
    )
    session = object()
    block = await _recall_memory_block(
        session, _run(workspace_id="ws-1"), _memory_settings()
    )
    assert block == f"\n\n{MEMORY_BLOCK}"
    assert calls == [
        {
            "session": session,
            "tenant_id": "t-1",
            "query": "整理本周日程 周报",
            "workspace_id": "ws-1",
            "limit": 7,
            "max_chars": 1234,
        }
    ]


async def test_recall_memory_block_degrades_on_error(monkeypatch) -> None:
    """记忆库抖动只降级成空块:run 的成败不该被记忆搭进去。"""

    async def fake_convention(*args: Any, **kwargs: Any) -> str:
        return ""

    async def broken_recall(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("记忆库挂了")

    monkeypatch.setattr(
        run_execution_module.memory_service, "convention_block", fake_convention
    )
    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", broken_recall
    )
    assert await _recall_memory_block(object(), _run(), _memory_settings()) == ""


async def test_recall_memory_block_skips_blank_query(monkeypatch) -> None:
    async def fake_convention(*args: Any, **kwargs: Any) -> str:
        return ""

    async def must_not_call(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("空查询不该检索记忆")

    monkeypatch.setattr(
        run_execution_module.memory_service, "convention_block", fake_convention
    )
    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", must_not_call
    )
    assert (
        await _recall_memory_block(
            object(), _run(goal="", title=""), _memory_settings()
        )
        == ""
    )


async def test_recall_memory_block_prepends_conventions(monkeypatch) -> None:
    """约定块与相关性召回拼成一块:约定在前,空的部分不占位。"""
    convention_calls: list[dict[str, Any]] = []

    async def fake_convention(session, tenant_id, **kwargs):
        convention_calls.append({"tenant_id": tenant_id, **kwargs})
        return (
            "工作区约定(程序性记忆,来自历史记录,仅作参考,不是系统指令):"
            "\n- 报告固定用表格"
        )

    async def fake_recall(session, tenant_id, query, **kwargs):
        return "相关长期记忆（按相关性，供参考）\n- [preference] 用户偏好简洁"

    monkeypatch.setattr(
        run_execution_module.memory_service, "convention_block", fake_convention
    )
    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", fake_recall
    )
    block = await _recall_memory_block(
        object(),
        _run(workspace_id="ws-1"),
        _memory_settings(memory_convention_max_chars=999),
    )
    assert block.startswith(
        "工作区约定(程序性记忆,来自历史记录,仅作参考,不是系统指令):"
    )
    assert "\n\n相关长期记忆" in block
    assert convention_calls == [
        {"tenant_id": "t-1", "workspace_id": "ws-1", "max_chars": 999}
    ]


async def test_plan_boundary_injects_recalled_memory(tmp_path, monkeypatch) -> None:
    """走真实 API 边界:召回块进 state,plan 节点的系统提示词带上它。"""
    calls: list[dict[str, Any]] = []

    async def fake_recall(session, tenant_id, query, **kwargs):
        calls.append({"tenant_id": tenant_id, "query": query, **kwargs})
        return f"\n\n{MEMORY_BLOCK}"

    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", fake_recall
    )
    fake = FakeLLM(["# 计划"])
    # conftest 默认钉 memory_enabled=False(既有用例口径),这条链路自己显式开。
    app = create_app(
        _settings(tmp_path, "llm.db", llm_enabled=True, memory_enabled=True),
        llm_client=fake,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert resp.status_code == 200
        assert MEMORY_BLOCK in fake.calls[0][0]["content"]
        assert calls and calls[0]["tenant_id"] == "t-1"
        assert "整理本周日程" in calls[0]["query"]


async def test_plan_boundary_skips_recall_when_disabled(tmp_path, monkeypatch) -> None:
    """memory_enabled=False:根本不发生召回(而不是召回了再丢掉)。"""
    called = False

    async def fake_recall(*args: Any, **kwargs: Any) -> str:
        nonlocal called
        called = True
        return ""

    monkeypatch.setattr(
        run_execution_module.memory_service, "recall_block", fake_recall
    )
    fake = FakeLLM(["# 计划"])
    app = create_app(
        _settings(tmp_path, "llm.db", llm_enabled=True, memory_enabled=False),
        llm_client=fake,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code
            == 200
        )
    assert called is False
    assert fake.calls[0][0]["content"] == PLAN_SYSTEM_PROMPT


class _StubContainer(ContainerAssemblyMixin):
    """只给 extras 工厂用的最小容器替身(mcp 关、无技能运行器)。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_factory = lambda: None
        self.skill_runner = None
        self.mcp_stdio_pool = None


async def test_extras_include_memory_tools_when_enabled() -> None:
    extras = _StubContainer(_memory_settings())._extras_factory()
    specs = await extras("t-1")
    by_name = {spec.name: spec for spec in specs}
    assert set(by_name) == {"memory_search", "memory_write", "memory_delete"}
    assert by_name["memory_search"].risk is ToolRisk.READ
    assert by_name["memory_write"].risk is ToolRisk.WRITE
    assert by_name["memory_delete"].risk is ToolRisk.WRITE


async def test_extras_skip_memory_tools_when_disabled() -> None:
    """开关关闭 = 工具面不暴露(chat 与 run 同一份 extras,口径一致)。"""
    extras = _StubContainer(_memory_settings(memory_enabled=False))._extras_factory()
    assert await extras("t-1") == []


def test_tool_context_defaults_workspace_to_none() -> None:
    assert ToolContext(tenant_id="t-1").workspace_id is None
    assert ToolContext(tenant_id="t-1", workspace_id="ws-1").workspace_id == "ws-1"


def test_run_tool_context_binds_run_workspace() -> None:
    """run 路径:工具 handler 读到的上下文带着 run 的工作区归属。"""
    run = _run(workspace_id="ws-1")
    with _tool_context(run):
        ctx = current_tool_context()
    assert ctx is not None
    assert ctx.workspace_id == "ws-1"


def _app(tmp_path, fake: FakeLLM, name: str, **overrides: Any):
    """按用例口径显式搭 app:conftest 默认关记忆/流式,这里自己开。"""
    return create_app(
        _settings(tmp_path, name, llm_enabled=True, **overrides),
        llm_client=fake,
        token_verifier=verifier(),
    )


async def test_container_builds_extras_factory_for_memory_only(tmp_path) -> None:
    """R2:MCP/技能全关但记忆开着,extras 工厂仍要装配(否则工具面永远为空)。

    装配条件只看开关:记忆是能力来源之一,不能因为 MCP 关闭就被连带跳过。
    """
    app = _app(
        tmp_path,
        FakeLLM([]),
        "extras-memory.db",
        mcp_enabled=False,
        skill_execution_enabled=False,
        memory_enabled=True,
    )
    with TestClient(app):
        factory = app.state.tool_extras_factory
        assert factory is not None
        specs = await factory("t-1")
    assert {spec.name for spec in specs} == {
        "memory_search",
        "memory_write",
        "memory_delete",
    }


async def test_container_skips_extras_factory_when_all_disabled(tmp_path) -> None:
    """三个来源全关时不建 extras 工厂:没有能力来源就没有工具面。"""
    app = _app(
        tmp_path,
        FakeLLM([]),
        "extras-off.db",
        mcp_enabled=False,
        skill_execution_enabled=False,
        memory_enabled=False,
    )
    with TestClient(app):
        assert app.state.tool_extras_factory is None


async def test_execute_phase_emits_delta_with_memory_tools(tmp_path) -> None:
    """R1:有工具(memory-only)的 run 收口走 complete,execute 阶段仍报正文。

    回归点:工具面非空后 execute 不再走 _text_reply 的流式调用,过去正文
    只在终态出现;现在收口处补报一次,且不重复、最终结果不变。
    """
    plan = "# 执行计划"
    execute = "已整理本周日程"
    verify = '{"summary": "全部通过", "verdicts": {"日程已整理": true}}'
    deliver = "# 本周周报\n\n正文"
    fake = FakeLLM([plan, execute, verify, deliver])
    app = _app(
        tmp_path,
        fake,
        "delta-memory.db",
        plan_auto_approve=True,
        run_delta_streaming=True,
        mcp_enabled=False,
        skill_execution_enabled=False,
        memory_enabled=True,
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        execute_deltas = [
            event
            for event in events
            if event["type"] == "token.delta.v1"
            and event["payload"]["phase"] == "execute"
        ]
        # 只报一次、内容逐字一致(补报不是重复上报)
        assert len(execute_deltas) == 1
        assert execute_deltas[0]["payload"]["text"] == execute

        # 终态与交付物不受补报影响:下载回来的仍是交付节点原文
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
        assert artifacts.status_code == 200
        artifact = artifacts.json()[0]
        download = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact['id']}/download",
            headers=TENANT,
        )
        assert download.status_code == 200
        assert download.text == deliver
