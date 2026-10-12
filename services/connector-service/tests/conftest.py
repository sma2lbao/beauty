"""测试装配:每用例独立 sqlite 库;传输工厂注入 FakeTransport,零网络。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from connector.services.mail import EmailSummary, TestResult

TENANT = {"X-Tenant-Id": "t-1"}


class FakeTransport:
    """假传输:固定探活结果与邮件列表,记录 fetch 调用的 limit。"""

    def __init__(
        self,
        test_result: TestResult | None = None,
        emails: list[EmailSummary] | None = None,
    ):
        self.test_result = test_result or TestResult(ok=True, detail="假连接正常")
        self.emails = emails or []
        self.fetch_limits: list[int] = []

    async def test(self) -> TestResult:
        return self.test_result

    async def fetch(self, limit: int) -> list[EmailSummary]:
        self.fetch_limits.append(limit)
        return self.emails[:limit]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        _settings(tmp_path), transport_factory=lambda conn: FakeTransport()
    )
    with TestClient(app) as test_client:
        yield test_client
