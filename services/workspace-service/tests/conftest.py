"""测试装配:每用例独立 sqlite 库 + 独立对象存储目录;鉴权开 + 真 token(§10)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from common.errors import NotFoundError, UpstreamError
from common.testing import TestIdP
from fastapi.testclient import TestClient

from workspace.core.config import Settings
from workspace.main import create_app
from workspace.services.sandbox_provider import ExecResult, SandboxSpec

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


def _settings(tmp_path: Path, name: str = "test.db", **overrides) -> Settings:
    overrides.setdefault("sandbox_reaper_enabled", False)
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}",
        auto_create_tables=True,
        artifact_root=str(tmp_path / "objects"),
        **overrides,
    )


class FakeSandboxProvider:
    """零网络/零 runtime 的 provider 替身:内存文件表 + 固定执行输出。"""

    provider = "fake"

    def __init__(self) -> None:
        self.created: list[SandboxSpec] = []
        self.destroyed: list[str] = []
        self.files: dict[str, dict[str, bytes]] = {}
        self.fail_create = False
        self.fail_exec = False
        self.fail_destroy = False
        self.exit_code = 0
        self.stdout = "hello from sandbox\n"
        self.stderr = ""
        self.truncated = False
        self.last_exec: dict[str, object] = {}

    async def create(self, spec: SandboxSpec) -> None:
        if self.fail_create:
            raise UpstreamError("sandbox.provider_error", "模拟创建失败")
        self.created.append(spec)
        self.files.setdefault(spec.name, {})

    async def exec(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        if self.fail_exec:
            raise UpstreamError("sandbox.provider_error", "模拟执行失败")
        self.last_exec = {
            "name": name,
            "argv": argv,
            "cwd": cwd,
            "env": env,
            "stdin": stdin,
            "timeout_seconds": timeout_seconds,
        }
        return ExecResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=7,
            truncated=self.truncated,
        )

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        self.files.setdefault(name, {})[path] = data

    async def read_file(self, name: str, path: str) -> bytes:
        try:
            return self.files[name][path]
        except KeyError as exc:
            raise NotFoundError(
                "sandbox.file_not_found",
                "文件不存在",
                details={"path": path},
            ) from exc

    async def destroy(self, name: str) -> None:
        if self.fail_destroy:
            raise UpstreamError("sandbox.provider_error", "模拟销毁失败")
        self.destroyed.append(name)
        self.files.pop(name, None)


@pytest.fixture()
def sandbox_provider() -> FakeSandboxProvider:
    return FakeSandboxProvider()


@pytest.fixture()
def client(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> Iterator[TestClient]:
    app = create_app(
        _settings(tmp_path),
        token_verifier=_IDP.verifier(),
        sandbox_provider=sandbox_provider,
    )
    with TestClient(app) as test_client:
        yield test_client


def _create_artifact(
    client: TestClient, title: str = "周报", content: str = "# 周报\n要点", **extra
):
    return client.post(
        "/v1/artifacts",
        json={"title": title, "content": content, **extra},
        headers=TENANT,
    )
