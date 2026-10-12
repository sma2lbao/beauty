"""测试装配:每个测试一个独立的 sqlite 文件库,建表走 lifespan(auto_create_tables)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app


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


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
        sse_poll_interval_seconds=0.05,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
