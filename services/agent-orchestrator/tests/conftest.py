"""测试装配:共享 fakes(FakeLLM/FakeKnowledge/FakeDangerTool)与应用装配助手。

- 每个测试独立 sqlite 文件库,建表走 lifespan(auto_create_tables);
- LLM 与工具全部注入 fake:零网络、零 token;
- 鉴权开且验真 token(架构 §10):TestIdP 本地密钥,JWKS 走 MockTransport,
  测试请求一律带 Bearer(租户在 claim 里),不再用 X-Tenant-Id 临时头;
- 契约由 duck typing 满足(见 services/llm.py、services/knowledge.py 的 Protocol)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from common.errors import UpstreamError
from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP
from common.tools import validate_arguments
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolRisk, ToolSpec, build_tools

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def bearer(tenant: str) -> dict[str, str]:
    """按租户签 Bearer 头(测试显式指明调用方租户)。"""
    return _IDP.bearer(tenant)


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


class FakeLLM:
    """按调用次序吐回复:字符串自动包成 LLMReply(content=...)。"""

    def __init__(
        self, responses: list[str | LLMReply], fail_on_call: int | None = None
    ):
        self.responses = list(responses)
        self.fail_on_call = fail_on_call
        self.calls: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]] | None] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMReply:
        self.calls.append(messages)
        self.tools_seen.append(tools)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("模型挂了")
        response = self.responses[len(self.calls) - 1]
        return (
            response if isinstance(response, LLMReply) else LLMReply(content=response)
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """流式:复用 complete 的脚本(调用序号与失败注入一致),按字切分。

        与真实网关一致:内容先整段算出,再逐段吐出——测试因此能断言
        "第几段该是什么",而不必关心切分粒度以外的实现细节。
        """
        reply = await self.complete(messages, model=model, tools=tools)
        for char in reply.content:
            yield char


class FakeDangerTool:
    """合成 DANGER 工具:对外不可逆写操作(验证确认闸,不绑定具体产品能力)。"""

    name = "external_publish"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    async def _handle(self, args: dict[str, Any]) -> str:
        self.calls.append(args)
        if self.fail:
            raise RuntimeError("发布通道挂了")
        self.sent.append(args)
        return f"已发布:{args.get('title', '')}"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="对外不可逆写操作(测试用)",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            handler=self._handle,
            risk=ToolRisk.DANGER,
        )


#: ask_question 的入参 schema(tool-service 目录描述的简化版,够测参数预校验)。
ASK_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                    "header": {"type": "string", "maxLength": 12},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "maxLength": 80},
                                "description": {
                                    "type": "string",
                                    "maxLength": 300,
                                },
                            },
                            "required": ["label"],
                        },
                    },
                    "multi_select": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        "allow_custom": {"type": "boolean"},
    },
    "required": ["questions"],
}

#: 默认两问:断言 interrupt payload 原样带出 questions。
DEFAULT_ASK_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "汇报周期?",
        "header": "周期",
        "options": [{"label": "每周"}, {"label": "每月"}],
    },
    {
        "question": "同步时间?",
        "header": "时间",
        "options": [{"label": "周一"}, {"label": "周三"}],
    },
]


def ask_reply(
    *,
    call_id: str = "call_ask",
    questions: list[dict[str, Any]] | None = None,
    allow_custom: bool | None = None,
) -> LLMReply:
    """构造一次 ask_question 工具调用(LLM 侧脚本用)。"""
    payload: dict[str, Any] = {"questions": questions or DEFAULT_ASK_QUESTIONS}
    if allow_custom is not None:
        payload["allow_custom"] = allow_custom
    call = ToolCall(
        id=call_id,
        name="ask_question",
        arguments=json.dumps(payload, ensure_ascii=False),
    )
    return LLMReply(content="", tool_calls=(call,))


class FakeInteractionToolkit:
    """假工具注册表:ask_question 是 interaction 类,其余工具是 function 类。

    - kind()/validate_call() 覆盖 ToolkitPort 的新钩子;
    - with_interaction() 打开交互工具可见性——execute 节点会调它,task 图因此
      看得到 ask_question;chat 只读路径不调,自然看不到;
    - execute 一律回 tool.not_supported:ask 不该走执行路径,测试据此断言
      "interrupt 前零副作用"。
    """

    def __init__(self, *, interaction: bool = False) -> None:
        self._interaction = interaction
        self._allowed: frozenset[str] | None = None
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.validated: list[tuple[str, str]] = []

    def _clone(self, *, interaction: bool | None = None) -> FakeInteractionToolkit:
        clone = FakeInteractionToolkit(
            interaction=self._interaction if interaction is None else interaction
        )
        clone._allowed = self._allowed
        clone.executed = self.executed
        clone.validated = self.validated
        return clone

    async def ensure_ready(self) -> None:
        return None

    def __len__(self) -> int:
        return len(self.names)

    @property
    def names(self) -> list[str]:
        names = ["ask_question"] if self._interaction else []
        if self._allowed is not None:
            names = [name for name in names if name in self._allowed]
        return names

    @property
    def has_dangerous(self) -> bool:
        return False

    def risk(self, name: str) -> ToolRisk:
        return ToolRisk.READ

    def kind(self, name: str) -> str:
        return "interaction" if name == "ask_question" else "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        self.validated.append((name, arguments))
        if name != "ask_question":
            return []
        try:
            payload = json.loads(arguments or "{}")
        except ValueError as exc:
            return [f"工具参数不是合法 JSON: {exc}"]
        if not isinstance(payload, dict):
            return ["工具参数必须是 JSON 对象"]
        return [
            error.message for error in validate_arguments(ASK_QUESTION_SCHEMA, payload)
        ]

    def schemas(self) -> list[dict[str, Any]]:
        if not self._interaction:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "ask_question",
                    "description": "向用户提问(测试用)",
                    "parameters": ASK_QUESTION_SCHEMA,
                },
            }
        ]

    def extended(self, specs: list[ToolSpec]) -> FakeInteractionToolkit:
        return self

    def restricted(self, allowed: list[str]) -> FakeInteractionToolkit:
        clone = self._clone()
        clone._allowed = frozenset(allowed)
        return clone

    def with_interaction(self) -> FakeInteractionToolkit:
        return self._clone(interaction=True)

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        self.executed.append((name, {"arguments": arguments, "approved": approved}))
        return json.dumps(
            {
                "ok": False,
                "error": {"code": "tool.not_supported", "message": "测试不执行工具"},
            },
            ensure_ascii=False,
        )


class FakeKnowledge:
    """假知识库:内存命中,记录检索调用(零网络)。"""

    def __init__(
        self,
        hits: list[dict] | None = None,
        fail: bool = False,
        provider: str = "local",
    ):
        self.hits = (
            hits
            if hits is not None
            else [
                {
                    "title": "客户 A 合同",
                    "score": 0.93,
                    "content": "合同评审安排在周四,需要准备报价单。",
                }
            ]
        )
        self.fail = fail
        self.provider = provider
        self.queries: list[tuple[str, str, int]] = []
        self.ingested: list[dict] = []
        self.documents: list[dict] = []

    async def search(self, tenant_id: str, query: str, limit: int) -> dict:
        self.queries.append((tenant_id, query, limit))
        if self.fail:
            raise RuntimeError("知识库挂了")
        return {
            "query": query,
            "provider": self.provider,
            "model": "hashing-256",
            "hits": self.hits[:limit],
        }

    # ---- 摄取(§8):outbox 消费者用 ----

    async def ingest_document(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict:
        if self.fail:
            raise RuntimeError("知识库挂了")
        self.ingested.append(
            {
                "tenant_id": tenant_id,
                "title": title,
                "content": content,
                "source": source,
            }
        )
        document = {
            "id": f"doc-{len(self.ingested)}",
            "title": title,
            "source": source,
        }
        self.documents.append(document)
        return document

    async def list_documents(self, tenant_id: str, *, limit: int = 50) -> list[dict]:
        if self.fail:
            raise RuntimeError("知识库挂了")
        return [
            doc for doc in self.documents if doc.get("tenant_id") in (None, tenant_id)
        ][:limit]


class RecordingBus:
    """记录投递事件的进程内总线替身(可注入失败以验证 outbox 重试)。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.received: list[Any] = []
        self.fail_times = fail_times
        self.calls = 0

    async def publish(self, envelope: Any) -> int:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("消费者不可用")
        self.received.append(envelope)
        return 1

    def subscribe(self, pattern: str, handler: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def types(self) -> list[str]:
        return [envelope.type for envelope in self.received]


class FakeWorkspace:
    """假工作区:记录交付物镜像与对象读写调用,可注入失败(零网络)。

    P3 技能包:put_object 返回固定 sha(测试拿它断言 run 快照),
    key 按租户前缀 + 序号生成(与 workspace-service 契约一致);
    objects 用于 get_object 往返。fail=True 时所有调用都失败。

    工作区归属:default_workspace 固定返回 default_id(记录 default_calls),
    get_workspace 只在 workspaces 里登记过的 id 上命中(未登记 → None 即 404),
    调用参数都记进 workspace_gets,便于断言"是否真的做了存在性校验"。
    """

    def __init__(
        self,
        fail: bool = False,
        *,
        workspaces: dict[str, dict] | None = None,
        default_id: str = "ws-default",
    ):
        self.fail = fail
        self.workspaces = workspaces or {}
        self.default_id = default_id
        self.created: list[dict] = []
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        self.gets: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.default_calls: list[str] = []
        self.workspace_gets: list[tuple[str, str]] = []
        self.sha256 = "a" * 64

    async def default_workspace(self, tenant_id: str) -> dict | None:
        """租户默认工作区(与 workspace-service GET /workspaces/default 同形)。"""
        self.default_calls.append(tenant_id)
        if self.fail:  # 与真实客户端同形:上游不可达 → 502 workspace.upstream_error
            raise UpstreamError("workspace.upstream_error", "工作区服务不可达")
        return {"id": self.default_id, "name": "默认工作区", "status": "active"}

    async def get_workspace(self, tenant_id: str, workspace_id: str) -> dict | None:
        """按 id 取工作区;未登记的 id 返回 None(服务层翻译成 404)。"""
        self.workspace_gets.append((tenant_id, workspace_id))
        if self.fail:  # 与真实客户端同形:上游不可达 → 502 workspace.upstream_error
            raise UpstreamError("workspace.upstream_error", "工作区服务不可达")
        return self.workspaces.get(workspace_id)

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str | None = None,
        run_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        data: bytes | None = None,
        workspace_id: str | None = None,
    ) -> dict:
        if self.fail:
            raise RuntimeError("工作区镜像失败")
        record = {
            "id": f"ws-art-{len(self.created) + 1}",
            "tenant_id": tenant_id,
            "title": title,
            "kind": kind,
            "content": content,
            "run_id": run_id,
            "filename": filename,
            "content_type": content_type,
            "data": data,
            "workspace_id": workspace_id,
        }
        self.created.append(record)
        return record

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict:
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        key = f"{tenant_id}/objects/skill-{len(self.puts) + 1}.zip"
        self.puts.append(
            {
                "tenant_id": tenant_id,
                "key": key,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": self.sha256,
                "data": data,
            }
        )
        self.objects[key] = data
        return {"key": key, "size_bytes": len(data), "sha256": self.sha256}

    async def get_object(self, tenant_id: str, key: str) -> bytes:
        self.gets.append((tenant_id, key))
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        if key not in self.objects:
            raise RuntimeError("对象不存在")
        return self.objects[key]

    async def delete_object(self, tenant_id: str, key: str) -> None:
        self.deleted.append((tenant_id, key))
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        self.objects.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_process_budget() -> Iterator[None]:
    """token 预算是进程级配置:多个 create_app 共用一个进程时,用例间必须复位。

    不做这一步,某个开了预算的用例会把后续用例的 run 全带成 failed。
    """
    usage_service.configure_budget(0)
    yield
    usage_service.configure_budget(0)


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
    # 内部服务地址:默认 127.0.0.1:8001,装配配额/用量时会建真实 httpx 客户端;
    # 禁网门禁下这会变成 SocketBlockedError 放大成 run failed。测试要覆盖
    # 配额/上报就自己 configure_client/configure_reporter(MockTransport)。
    "account_service_url": "",
    # 计划审批:关掉自动放行——既有用例覆盖的是"人工审批"路径;
    # 自动放行由 tests/test_plan_auto_approve.py 显式开启。
    "plan_auto_approve": False,
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
