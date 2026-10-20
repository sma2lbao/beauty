"""测试装配:共享 fakes(FakeLLM/FakeConnector)与应用装配助手。

- 每个测试独立 sqlite 文件库,建表走 lifespan(auto_create_tables);
- LLM 与工具全部注入 fake:零网络、零 token;
- 鉴权开且验真 token(架构 §10):TestIdP 本地密钥,JWKS 走 MockTransport,
  测试请求一律带 Bearer(租户在 claim 里),不再用 X-Tenant-Id 临时头;
- 契约由 duck typing 满足(见 services/llm.py、services/connectors.py 的 Protocol)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from common.testing import TestIdP
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.tools import build_tools

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


class FakeConnector:
    """假连接器:内存连接与邮件,记录调用(工具测试零网络)。"""

    def __init__(
        self,
        connections: list[dict] | None = None,
        emails: list[dict] | None = None,
        fail: bool = False,
    ):
        self.connections = (
            connections
            if connections is not None
            else [
                {
                    "id": "conn-1",
                    "account": "me@qq.com",
                    "provider": "imap",
                    "status": "active",
                }
            ]
        )
        self.emails = (
            emails
            if emails is not None
            else [
                {"subject": "周报", "sender": "a@b.c", "sent_at": "D", "snippet": "s"}
            ]
        )
        self.fail = fail
        self.calls: list[tuple] = []
        self.sent: list[tuple[str, str, str]] = []

    async def list_connections(self, tenant_id: str) -> list[dict]:
        self.calls.append(("list_connections", tenant_id))
        if self.fail:
            raise RuntimeError("连接器挂了")
        return self.connections

    async def fetch_emails(
        self, tenant_id: str, connection_id: str, limit: int
    ) -> list[dict]:
        self.calls.append(("fetch_emails", tenant_id, connection_id, limit))
        if self.fail:
            raise RuntimeError("连接器挂了")
        return self.emails[:limit]

    async def send_email(
        self,
        tenant_id: str,
        connection_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        self.calls.append(("send_email", tenant_id, connection_id, to))
        if self.fail:
            raise RuntimeError("连接器挂了")
        self.sent.append((to, subject, body))
        return f"已发送至 {to}"


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
    """假工作区:记录交付物镜像调用,可注入失败(零网络)。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.created: list[dict] = []

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str,
        run_id: str | None = None,
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
        }
        self.created.append(record)
        return record


def _settings(tmp_path: Path, name: str = "test.db", **overrides: Any) -> Settings:
    """测试用配置:自足、零网络。

    两道防线缺一不可:

    1. _env_file=None:不读本地 .env(开发机配置,可能开着 LLM_ENABLED 等);
    2. **显式钉住所有"会连外部"的开关**:Nx 会把项目 .env 注入任务环境,
       环境变量优先级高于 .env 与 _env_file=None——只靠第 1 条挡不住
       (npx nx test 会因此真的调用模型网关)。显式入参才是最高优先级。

    需要开这些能力的测试通过 overrides 显式传入(如 _client 的 llm_enabled=True)。
    """
    defaults: dict[str, Any] = {
        "_env_file": None,
        "env": "dev",
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/{name}",
        "auto_create_tables": True,
        "sse_poll_interval_seconds": 0.05,
        # 外部副作用开关:默认全关,测试要开就显式传
        "llm_enabled": False,
        "tools_enabled": False,
        "outbox_enabled": False,
        "knowledge_ingest_enabled": False,
        "workspace_enabled": False,
        "sandbox_enabled": False,
        "jobs_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client(tmp_path: Path, fake: FakeLLM) -> TestClient:
    """LLM 路径(无工具):plan/execute/verify/deliver 全走 fake。"""
    app = create_app(
        _settings(tmp_path, "llm.db", llm_enabled=True),
        llm_client=fake,
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _tool_client(tmp_path: Path, fake: FakeLLM, connector: FakeConnector) -> TestClient:
    """LLM + 工具路径:execute 的 function-calling 打到假连接器。"""
    app = create_app(
        _settings(tmp_path, "tools.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(tenant, connector=connector),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _create_run(client: TestClient, goal: str = "汇总本周邮件") -> str:
    resp = client.post(
        "/api/v1/runs",
        json={"title": "周报", "goal": goal, "acceptance_criteria": ["邮件已汇总"]},
        headers=TENANT,
    )
    return resp.json()["id"]


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path), token_verifier=_IDP.verifier())
    with TestClient(app) as test_client:
        yield test_client
