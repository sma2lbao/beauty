"""沙箱提供方:scope 复用粒度 + HTTP 适配 + 运行时装配(零网络)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.clients.sandbox import SandboxExecResult, SandboxInfo

from tests.conftest import _settings, read_envelope
from tool_service.services.sandbox import (
    HttpSandboxProvider,
    SandboxRuntime,
    scope_key_for,
)


def test_scope_key_precedence() -> None:
    assert (
        scope_key_for(
            "t-1", sandbox_scope="custom:1", conversation_id="c-1", run_id="r-1"
        )
        == "custom:1"
    )
    assert (
        scope_key_for("t-1", conversation_id="c-1", run_id="r-1") == "conversation:c-1"
    )
    assert scope_key_for("t-1", run_id="r-1") == "run:r-1"
    assert scope_key_for("t-1") == "tenant:t-1"


class FakeSandbox:
    """common.clients.sandbox.Sandbox 的最小替身,只记录调用。"""

    def __init__(self) -> None:
        self.ensured: list[dict[str, Any]] = []
        self.execs: list[dict[str, Any]] = []
        self.touched: list[str] = []
        self.closed = False

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
        self.ensured.append(
            {
                "tenant_id": tenant_id,
                "scope_key": scope_key,
                "ttl_seconds": ttl_seconds,
                "run_id": run_id,
            }
        )
        return SandboxInfo(id="sbx-9", name="bm-9", status="running"), True

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
        self.execs.append(
            {
                "tenant_id": tenant_id,
                "sandbox_id": sandbox_id,
                "argv": argv,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SandboxExecResult(
            exit_code=0,
            stdout=read_envelope(),
            stderr="",
            duration_ms=3,
            truncated=False,
        )

    async def touch(self, tenant_id: str, sandbox_id: str) -> None:
        self.touched.append(sandbox_id)

    async def aclose(self) -> None:
        self.closed = True


async def test_http_provider_ensure_returns_id_and_slides_ttl() -> None:
    fake = FakeSandbox()
    provider = HttpSandboxProvider(sandbox=fake, ttl_seconds=99)

    sandbox_id = await provider.ensure("t-1", scope_key="run:r-1", run_id="r-1")

    assert sandbox_id == "sbx-9"
    assert fake.ensured == [
        {
            "tenant_id": "t-1",
            "scope_key": "run:r-1",
            "ttl_seconds": 99,
            "run_id": "r-1",
        }
    ]


async def test_http_provider_passes_stdin_and_timeout() -> None:
    fake = FakeSandbox()
    provider = HttpSandboxProvider(sandbox=fake)

    result = await provider.exec(
        "t-1",
        "sbx-9",
        ["python3", "-c", "print(1)"],
        stdin="{}",
        timeout_seconds=7,
    )

    assert result.exit_code == 0
    assert fake.execs[0]["stdin"] == "{}"
    assert fake.execs[0]["timeout_seconds"] == 7


async def test_http_provider_touch_and_close() -> None:
    fake = FakeSandbox()
    provider = HttpSandboxProvider(sandbox=fake)

    await provider.touch("t-1", "sbx-9")
    await provider.aclose()

    assert fake.touched == ["sbx-9"]
    assert fake.closed is True


async def test_runtime_builds_http_provider_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auth_issuer="http://idp.test")
    runtime = SandboxRuntime(settings=settings)

    assert isinstance(runtime.provider, HttpSandboxProvider)
    assert runtime.provider.ttl_seconds == settings.sandbox_tool_ttl_seconds

    await runtime.aclose()
