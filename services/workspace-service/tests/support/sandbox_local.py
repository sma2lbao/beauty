"""LocalSandboxProvider 测试的共享夹具与小工具。

W5 测试拆分后由 `test_sandbox_provider_local.py` 与
`test_sandbox_provider_local_files.py` 共用:沙箱根一律落在 tmp_path 下,零网络。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from workspace.services.sandbox_provider import (
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    SandboxSpec,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="用例依赖 POSIX shell / 权限位 / 符号链接语义",
)

NAME = "bm-local"
MAX_OUTPUT_BYTES = 1024


def _spec(name: str = NAME) -> SandboxSpec:
    return SandboxSpec(
        tenant_id="t-1",
        name=name,
        kind="generic",
        image="python",
        cpus=1.0,
        memory_mb=512,
        network="deny",
        ttl_seconds=600,
    )


def _dir_of(root: Path, name: str = NAME) -> Path:
    """沙箱宿主目录;按 provider 的 resolve 语义对齐(macOS /var→/private/var)。"""
    return (root / name).resolve()


def _workspace_of(root: Path, name: str = NAME) -> Path:
    return _dir_of(root, name) / LOCAL_WORKSPACE_SUBDIR


@pytest.fixture()
def local_root(tmp_path: Path) -> Path:
    return tmp_path / "sb"


@pytest.fixture()
def provider(local_root: Path) -> LocalSandboxProvider:
    return LocalSandboxProvider(
        root=str(local_root),
        max_output_bytes=MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds=7,
    )


@pytest.fixture()
async def sandbox(provider: LocalSandboxProvider) -> str:
    await provider.create(_spec())
    return NAME
