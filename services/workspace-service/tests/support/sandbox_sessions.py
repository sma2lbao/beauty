"""持久 shell 会话测试的共享工具:直接驱动 `ShellSessionManager` 打开会话。

W5 测试拆分后 `test_sandbox_sessions_manager.py` 与
`test_sandbox_sessions_manager_edges.py` 共用这一小段打开逻辑,
避免两个文件各复制一份。
"""

from __future__ import annotations

from tests.conftest import FakeSandboxProvider
from workspace.services.shell_sessions import ShellSessionManager


async def _open_managed(
    manager: ShellSessionManager,
    provider: FakeSandboxProvider,
    *,
    tenant_id: str = "t-1",
    sandbox_id: str = "sb-1",
    runtime_name: str = "bm-1",
    cwd: str = "/workspace",
):
    """在管理器中开一个会话,返回 (会话信息, 夹具记录的 shell 对象)。"""
    info = await manager.open(
        tenant_id=tenant_id,
        sandbox_id=sandbox_id,
        runtime_name=runtime_name,
        provider=provider,
        cwd=cwd,
    )
    return info, provider.shells[-1]
