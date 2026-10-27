"""测试装配:每用例独立 sqlite 库 + 独立对象存储目录;鉴权开 + 真 token(§10)。

库/客户端/settings 样板来自 common.service_testkit;沙箱 provider 是服务专属替身。
"""

from __future__ import annotations

import pytest
from common.errors import NotFoundError, UpstreamError
from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP
from fastapi.testclient import TestClient

from workspace.core.config import Settings
from workspace.main import create_app
from workspace.services.sandbox_provider import ExecResult, FileEntry, SandboxSpec

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


_settings = settings_factory(
    Settings,
    defaults={
        "sandbox_reaper_enabled": False,
        # 显式钉回测试意图的镜像别名:本地 .env 会经 uv run 注入
        # WORKSPACE__SANDBOX_DEFAULT_IMAGE(指向本地 rootfs),不钉就依赖机器配置
        "sandbox_default_image": "python",
        "artifact_root": lambda tmp: str(tmp / "objects"),
    },
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
        # 文件树:按沙箱名预置条目;list_dir 按 path 前缀 + depth 过滤后排序返回
        self.tree: dict[str, list[FileEntry]] = {}
        self.list_calls: list[tuple[str, str, int]] = []
        self.fail_list = False
        self.list_error: Exception | None = None

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

    def add_entry(
        self,
        name: str,
        path: str,
        kind: str = "file",
        size: int = 0,
        modified: float | None = None,
    ) -> None:
        """给替身沙箱加一条文件树条目(测试里造树用)。"""
        self.tree.setdefault(name, []).append(
            FileEntry(path=path, kind=kind, size=size, modified=modified)
        )

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        if self.list_error is not None:
            raise self.list_error
        if self.fail_list:
            raise UpstreamError("sandbox.provider_error", "模拟列举失败")
        self.list_calls.append((name, path, depth))
        base = path.rstrip("/")
        prefix = f"{base}/"
        picked = [
            entry
            for entry in self.tree.get(name, [])
            if entry.path.startswith(prefix)
            and entry.path[len(prefix) :].count("/") < depth
        ]
        return sorted(picked, key=lambda entry: entry.path)

    async def destroy(self, name: str) -> None:
        if self.fail_destroy:
            raise UpstreamError("sandbox.provider_error", "模拟销毁失败")
        self.destroyed.append(name)
        self.files.pop(name, None)


@pytest.fixture()
def sandbox_provider() -> FakeSandboxProvider:
    return FakeSandboxProvider()


client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
    fixture_kwargs={"sandbox_provider": "sandbox_provider"},
)


def _create_artifact(
    client: TestClient, title: str = "周报", content: str = "# 周报\n要点", **extra
):
    return client.post(
        "/v1/artifacts",
        json={"title": title, "content": content, **extra},
        headers=TENANT,
    )
