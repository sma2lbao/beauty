"""应用装配:测试用 settings 工厂、client 夹具与应用装配助手。

- 每个测试独立 sqlite 文件库,建表走 lifespan(auto_create_tables);
- LLM 与工具全部注入 fake:零网络、零 token;
- 契约由 duck typing 满足(见 services/llm.py、services/knowledge.py 的 Protocol)。

本模块同时承载进程级状态复位夹具(token 预算、模型列表缓存)——它们是 autouse 的,
经 tests/conftest.py 注册后对所有用例生效。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from common.service_testkit import client_fixture, settings_factory
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services import models as models_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.tools import ToolSpec, build_tools
from tests.fixtures.auth import _IDP, TENANT
from tests.fixtures.knowledge import FakeKnowledge
from tests.fixtures.llm import FakeLLM


@pytest.fixture(autouse=True)
def _reset_process_budget() -> Iterator[None]:
    """token 预算是进程级配置:多个 create_app 共用一个进程时,用例间必须复位。

    不做这一步,某个开了预算的用例会把后续用例的 run 全带成 failed。
    """
    usage_service.configure_budget(0)
    yield
    usage_service.configure_budget(0)


@pytest.fixture(autouse=True)
def _reset_models_cache() -> Iterator[None]:
    """模型列表缓存是进程级的(P1-G §6.3):用例间必须复位,否则上一个用例
    拿到的网关结果会漏到下一个用例的白名单校验里。"""
    models_service.reset_models_cache()
    yield
    models_service.reset_models_cache()


#: AO 专属固定覆盖;共通三道防线(_env_file=None、env=dev、临时 sqlite +
#: auto_create_tables、NETWORK_SWITCHES 全关)由 common.service_testkit 提供。
_SETTINGS_DEFAULTS: dict[str, Any] = {
    "sse_poll_interval_seconds": 0.05,
    # 正文增量流:默认关——既有用例断言的是事件类型序列,打开会多出
    # token.delta.v1;要覆盖流式路径的用例显式传 True(tests/test_run_delta.py)
    "run_delta_streaming": False,
    # R-32 补钉:.env 会被 Nx 注入任务环境(AGENT_ORCHESTRATOR__* 前缀),
    # 这几项开着时 quota 用例的执行图会去连外部服务,被 pytest-socket 拦成
    # SocketBlockedError → run failed。默认钉 False,与直跑口径一致。
    "documents_enabled": False,
    "knowledge_enabled": False,
    "skill_execution_enabled": False,
    # 工具服务同理:开了会装配 RemoteToolkit(HTTP 到 :8008),执行图里的
    # 工具调用会被 pytest-socket 拦成 SocketBlockedError。需要工具路径的用例
    # 注入 toolkit_factory / tool_client 替身,或显式传 True。
    "tool_service_enabled": False,
    # 内部服务地址:默认 127.0.0.1:8001,装配配额/用量时会建真实 httpx 客户端;
    # 禁网门禁下这会变成 SocketBlockedError 放大成 run failed。测试要覆盖
    # 配额/上报就自己 configure_client/configure_reporter(MockTransport)。
    "account_service_url": "",
    # 计划审批:关掉自动放行——既有用例覆盖的是"人工审批"路径;
    # 自动放行由 tests/test_plan_auto_approve.py 显式开启。
    "plan_auto_approve": False,
    # 记忆模块(P0 压缩 + P1 长期记忆):默认关——既有用例断言的是"没有记忆
    # 工具、系统提示词没有记忆/摘要块"的旧口径;相关能力由 test_memory_*.py /
    # test_compaction.py 用显式 settings 开启。
    "memory_enabled": False,
    "context_compaction_enabled": False,
    # P1-F 上下文工程(设计 §7.1/§7.2):默认关——既有用例断言的是"工具面/系统
    # 提示词里没有上下文段"的旧口径;管线/offload/技能加载由 tests/test_context_*.py
    # 与 tests/test_container_context.py 用显式 settings 开启。
    "context_pipeline_enabled": False,
    "context_skill_loading_enabled": False,
    "context_offload_enabled": False,
    # P2 记忆二期:自动抽取/巩固/sweeper 默认关——后台任务与额外 LLM 调用会
    # 打乱既有用例的事件序列/调用计数;P2 用例自己显式开启。
    "memory_auto_extract_enabled": False,
    "memory_consolidation_enabled": False,
    "memory_sweep_interval_seconds": 0.0,
    # P1-G 模型路由:白名单默认空(=不校验,避免 Nx 注入的 .env 把用例卡成 400);
    # 图片预算显式钉默认值,让提示词形状断言不受宿主环境变量影响。
    # .env 里的 LLM_MODEL 同样会被 Nx 注入:钉 None 才是"走网关默认路由"口径,
    # 否则 GET /models 的 default_model 与请求体里的模型断言会随宿主环境漂移。
    "llm_model": None,
    "llm_models_cache_seconds": 0.0,
    "llm_available_models": [],
    "llm_image_max_count": 4,
    "llm_image_max_bytes": 5_242_880,
    "llm_image_max_total_bytes": 10_485_760,
}

#: 测试用配置:自足、零网络。保留 (tmp_path, name, **overrides) 签名,
#: 多个测试文件直接 `from tests.conftest import _settings` 复用。
_settings = settings_factory(Settings, defaults=_SETTINGS_DEFAULTS)


def _client(tmp_path: Path, fake: FakeLLM) -> TestClient:
    """LLM 路径(无工具):plan/execute/verify/deliver 全走 fake。"""
    app = create_app(
        _settings(tmp_path, "llm.db", llm_enabled=True),
        llm_client=fake,
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _tool_client(
    tmp_path: Path,
    fake: FakeLLM,
    knowledge: FakeKnowledge | None = None,
    *,
    extras: tuple[ToolSpec, ...] = (),
) -> TestClient:
    """LLM + 工具路径:execute 的 function-calling 打到假知识库(+ 合成工具)。"""
    knowledge = knowledge or FakeKnowledge()
    app = create_app(
        _settings(tmp_path, "tools.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(
            tenant, knowledge=knowledge
        ).extended(list(extras)),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _create_run(client: TestClient, goal: str = "整理本周日程") -> str:
    resp = client.post(
        "/api/v1/runs",
        json={"title": "周报", "goal": goal, "acceptance_criteria": ["日程已整理"]},
        headers=TENANT,
    )
    return resp.json()["id"]


#: 默认客户端:tmp_path 独立库 + 进入 lifespan + 每用例现造 verifier。
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)
