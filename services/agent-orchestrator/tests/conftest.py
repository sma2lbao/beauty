"""测试装配:共享 fakes(FakeLLM/FakeConnector)与应用装配助手。

- 每个测试独立 sqlite 文件库,建表走 lifespan(auto_create_tables);
- LLM 与工具全部注入 fake:零网络、零 token;
- 契约由 duck typing 满足(见 services/llm.py、services/connectors.py 的 Protocol)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.tools import build_tools

TENANT = {"X-Tenant-Id": "t-1"}


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


def _settings(tmp_path: Path, name: str = "test.db", **overrides: Any) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}",
        auto_create_tables=True,
        sse_poll_interval_seconds=0.05,
        **overrides,
    )


def _client(tmp_path: Path, fake: FakeLLM) -> TestClient:
    """LLM 路径(无工具):plan/execute/verify/deliver 全走 fake。"""
    app = create_app(_settings(tmp_path, "llm.db", llm_enabled=True), llm_client=fake)
    return TestClient(app)


def _tool_client(tmp_path: Path, fake: FakeLLM, connector: FakeConnector) -> TestClient:
    """LLM + 工具路径:execute 的 function-calling 打到假连接器。"""
    app = create_app(
        _settings(tmp_path, "tools.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(tenant, connector=connector),
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
    app = create_app(_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client
