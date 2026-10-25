"""测试装配:每用例独立 sqlite 库 + 假沙箱提供方(零网络)。

鉴权开 + 真 token(与其余服务同做法);沙箱是内存替身,stdout 直接给 helper
信封,这样目录/API/审计/幂等链路在没有 microsandbox 的环境里也能端到端测。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from common.clients.sandbox import SandboxExecResult
from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP

from tool_service.core.config import Settings
from tool_service.main import create_app
from tool_service.services.executors.http import HttpToolExecutor

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器(注入 create_app)。"""

    return _IDP.verifier()


_settings = settings_factory(
    Settings,
    defaults={
        "internal_secret": "test-internal-secret",
        "workspace_service_url": "http://workspace.test",
    },
)


def read_envelope(path: str = "a.txt") -> str:
    """成功信封(与 helpers/fs_helper.py 的 read 输出字段一致)。"""

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "total_lines": 2,
            "offset": 1,
            "limit": 200,
            "returned_lines": 2,
            "truncated": False,
            "next_offset": None,
            "bytes": 12,
            "encoding": "utf-8",
            "bom": False,
            "ends_with_newline": True,
            "sha256": "0" * 64,
            "content": "     1\thello\n     2\tworld\n",
            "notes": [],
        },
        ensure_ascii=False,
    )


def write_envelope(
    path: str = "a.txt",
    *,
    created: bool = True,
    changed: bool = True,
    bytes_written: int = 12,
    sha_before: str | None = None,
    sha_after: str = "1" * 64,
) -> str:
    """write/edit 成功信封(字段与 helpers/fs_helper.py 一致)。"""

    payload: dict[str, Any] = {
        "ok": True,
        "path": path,
        "created": created,
        "changed": changed,
        "bytes": bytes_written,
        "lines": 2,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "notes": [],
    }
    if not created:
        payload["replacements"] = 1
    return json.dumps(payload, ensure_ascii=False)


class FakeSandboxProvider:
    """零网络沙箱替身:记录 ensure/exec/touch,返回可配置结果。"""

    provider = "fake"

    def __init__(self) -> None:
        self.ensured: list[dict[str, Any]] = []
        self.exec_calls: list[dict[str, Any]] = []
        self.touched: list[str] = []
        self.closed = False
        self.sandbox_id = "sbx-1"
        self.stdout = read_envelope()
        self.stderr = ""
        self.exit_code = 0
        self.duration_ms = 5
        self.truncated = False
        self.ensure_error: Exception | None = None
        self.exec_error: Exception | None = None
        self.touch_error: Exception | None = None

    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        if self.ensure_error is not None:
            raise self.ensure_error
        self.ensured.append(
            {"tenant_id": tenant_id, "scope_key": scope_key, "run_id": run_id}
        )
        return self.sandbox_id

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        if self.exec_error is not None:
            raise self.exec_error
        self.exec_calls.append(
            {
                "tenant_id": tenant_id,
                "sandbox_id": sandbox_id,
                "argv": argv,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SandboxExecResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=self.duration_ms,
            truncated=self.truncated,
        )

    async def touch(self, tenant_id: str, sandbox_id: str) -> None:
        self.touched.append(sandbox_id)
        if self.touch_error is not None:
            raise self.touch_error

    async def aclose(self) -> None:
        self.closed = True


class FakeKnowledgeClient:
    """零网络知识库替身:记录检索调用,返回可配置结果/异常。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "query": "",
            "provider": "fake",
            "model": "fake",
            "hits": [],
        }
        self.error: Exception | None = None
        self.closed = False

    async def search(self, tenant_id: str, *, query: str, limit: int) -> dict[str, Any]:
        self.calls.append({"tenant_id": tenant_id, "query": query, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self) -> None:
        self.closed = True


class SlowSandboxProvider(FakeSandboxProvider):
    """exec/ensure 挂住不返回,用于超时分支。"""

    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        await asyncio.sleep(5)
        raise AssertionError("ensure 超时路径不应执行到这里")

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        await asyncio.sleep(5)
        raise AssertionError("exec 超时路径不应执行到这里")


@pytest.fixture()
def sandbox_provider() -> FakeSandboxProvider:
    return FakeSandboxProvider()


@pytest.fixture()
def knowledge_client() -> FakeKnowledgeClient:
    return FakeKnowledgeClient()


@pytest.fixture()
def http_executor(knowledge_client: FakeKnowledgeClient) -> HttpToolExecutor:
    return HttpToolExecutor(knowledge=knowledge_client)


client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
    fixture_kwargs={
        "sandbox_provider": "sandbox_provider",
        "http_executor": "http_executor",
    },
)
