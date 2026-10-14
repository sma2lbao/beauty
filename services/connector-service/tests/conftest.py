"""测试装配:每用例独立 sqlite 库;传输工厂注入 FakeTransport;鉴权开 + 真 token。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from connector.services.mail import EmailSummary, TestResult
from common.testing import TestIdP

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


class FakeTransport:
    """假传输:固定探活结果与邮件列表,记录 fetch 的 limit 与 send 调用。"""

    def __init__(
        self,
        test_result: TestResult | None = None,
        emails: list[EmailSummary] | None = None,
    ):
        self.test_result = test_result or TestResult(ok=True, detail="假连接正常")
        self.emails = emails or []
        self.fetch_limits: list[int] = []
        self.sent: list[tuple[str, str, str]] = []

    async def test(self) -> TestResult:
        return self.test_result

    async def fetch(self, limit: int) -> list[EmailSummary]:
        self.fetch_limits.append(limit)
        return self.emails[:limit]

    async def send(self, to: str, subject: str, body: str) -> str:
        self.sent.append((to, subject, body))
        return f"已发送至 {to}"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        _settings(tmp_path),
        transport_factory=lambda conn: FakeTransport(),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as test_client:
        yield test_client
