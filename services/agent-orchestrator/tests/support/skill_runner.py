"""P3-C 技能包沙箱执行:SkillRunner + main 的 extras factory(零网络/零真实沙箱)。

覆盖:
- execute 成功链路:create → 取包校验 sha → 写 zip/解包脚本 → 解包 → entrypoint
  → 列产物/读正文 → run_artifacts 登记 → destroy;
- 失败与边界:无包、sha 不一致、解包失败、entrypoint 退出码非 0、产物超限、
  逐文件登记(文本/二进制各一件)、登记失败不让工具失败、entrypoint 路径非法
  (防御快照被改);
- UNPACK_PY 本地联调(真实子进程):恶意 zip(../、绝对路径、符号链接、超量)拒绝;
- main 的 extras factory:run 上下文下暴露 skill_run、无包不暴露、chat 无 run_id
  不暴露、MCP 故障不阻断 skill;
- 图路径端到端:execute 请求 skill_run → 确认闸 → 沙箱执行 → completed。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.artifacts import FilesystemArtifactStore
from database.base import Base
from database.session import create_session_factory, session_scope

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import ArtifactRepository
from agent_orchestrator.services.sandbox import SandboxExecResult, SandboxInfo
from agent_orchestrator.services.skill_runner import (
    LIST_OUTPUTS_PATH,
    OUTPUT_ROOT,
    UNPACK_PATH,
    SkillRunner,
)
from tests.conftest import FakeWorkspace, _settings

MANIFEST: dict[str, Any] = {
    "id": "team.weekly-report",
    "version": "1.0.0",
    "title": "周报生成",
    "entrypoint": ["python", "scripts/run.py"],
    "runtime": "python",
    "network": "deny",
    "timeout_seconds": 120,
}
SKILL_MD = "# 周报生成\n\n按模板整理本周日程。\n"
PACKAGE_KEY = "t-1/objects/skill-1.zip"


def build_zip(
    files: dict[str, str | bytes],
    *,
    symlinks: dict[str, str] | None = None,
) -> bytes:
    """测试内构造 zip(可附加符号链接条目,模拟恶意包)。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16  # S_IFLNK
            archive.writestr(info, target)
    return buffer.getvalue()


def plain_package() -> bytes:
    return build_zip(
        {
            "SKILL.md": SKILL_MD,
            "skill.json": json.dumps(MANIFEST),
            "scripts/run.py": "print('hello')",
        }
    )


def _exec(exit_code: int = 0, stdout: str = "", stderr: str = "") -> SandboxExecResult:
    return SandboxExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
        truncated=False,
    )


class FakeSandbox:
    """Sandbox 协议替身:记录 create/write/exec/read/destroy,按 argv 路由结果。

    解包/列产物/entrypoint 三类 exec 分别有预置结果;测试改属性即可脚本化。
    """

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.execs: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.destroyed: list[str] = []
        self.unpack = _exec(stdout='{"ok": true, "files": 3, "bytes": 42}')
        self.entry = _exec(stdout="周报已生成")
        self.listing: dict[str, Any] | None = {
            "files": [{"path": "report.md", "size": len("# 报告".encode())}],
            "truncated": False,
        }
        self.listing_exit_code = 0
        self.output_files: dict[str, bytes] = {"report.md": "# 报告".encode()}
        self.read_error: Exception | None = None
        self.destroy_error: Exception | None = None

    async def create(self, tenant_id: str, **kwargs: Any) -> SandboxInfo:
        self.created.append({"tenant_id": tenant_id, **kwargs})
        index = len(self.created)
        return SandboxInfo(
            id=f"sb-{index}",
            name=f"bm-skill-{index}",
            status="running",
            kind=str(kwargs.get("kind") or "skill"),
            image=kwargs.get("image"),
            network=str(kwargs.get("network") or "deny"),
        )

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None:
        self.files[f"{sandbox_id}:{path}"] = data

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: Any,
        *,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        args = list(argv)
        self.execs.append(
            {
                "sandbox_id": sandbox_id,
                "argv": args,
                "cwd": cwd,
                "env": env,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        if len(args) > 1 and args[1] == UNPACK_PATH:
            return self.unpack
        if len(args) > 1 and args[1] == LIST_OUTPUTS_PATH:
            if self.listing_exit_code != 0:
                return _exec(exit_code=self.listing_exit_code, stderr="listing boom")
            return _exec(stdout=json.dumps(self.listing or {}))
        return self.entry

    async def read_file(self, tenant_id: str, sandbox_id: str, path: str) -> bytes:
        self.reads.append(path)
        if self.read_error is not None:
            raise self.read_error
        relative = (
            path[len(OUTPUT_ROOT) + 1 :] if path.startswith(OUTPUT_ROOT) else path
        )
        if relative not in self.output_files:
            raise RuntimeError(f"沙箱文件不存在:{path}")
        return self.output_files[relative]

    async def destroy(self, tenant_id: str, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)
        if self.destroy_error is not None:
            raise self.destroy_error

    async def aclose(self) -> None:
        return None


_UNSET = object()


@dataclass
class Env:
    """一次测试的执行环境(runner + fakes + 已落库的 run 快照)。"""

    runner: SkillRunner
    sandbox: FakeSandbox
    workspace: FakeWorkspace
    run_id: str
    factory: Any
    settings: Settings
    store: FilesystemArtifactStore | None
    package: bytes
    run: Run

    async def artifact(self) -> Any:
        async with session_scope(self.factory) as session:
            return await ArtifactRepository(session).find_for_run("t-1", self.run_id)

    async def artifacts(self) -> list[Any]:
        """该 run 的全部交付物(逐文件登记后可能多件)。"""
        async with session_scope(self.factory) as session:
            return await ArtifactRepository(session).list_for_run("t-1", self.run_id)

    async def close(self) -> None:
        await self.factory.engine.dispose()


@asynccontextmanager
async def make_env(
    tmp_path: Path,
    *,
    run_overrides: dict[str, Any] | None = None,
    settings_overrides: dict[str, Any] | None = None,
    sandbox: FakeSandbox | None = None,
    workspace: FakeWorkspace | None = None,
    store: Any = _UNSET,
    entrypoint: tuple[str, ...] = ("python", "scripts/run.py"),
    runtime: str = "python",
    network: str = "deny",
    timeout_seconds: int | None = 120,
) -> AsyncIterator[Env]:
    """建独立 sqlite 库 + run 快照 + runner;退出时释放 engine。

    对象字节放 FakeWorkspace,sha 用真实 digest(与 run 快照一致)——
    P3-B 落地时快照的 sha 就是对象存储返回的真实 sha256。
    """
    settings_kwargs: dict[str, Any] = {
        "artifact_store": "filesystem",
        "artifact_root": str(tmp_path / "artifacts"),
    }
    settings_kwargs.update(settings_overrides or {})
    settings = _settings(tmp_path, "runner.db", **settings_kwargs)
    factory = create_session_factory(settings.database_url)
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    package = plain_package()
    workspace = workspace or FakeWorkspace()
    workspace.objects[PACKAGE_KEY] = package
    sandbox = sandbox or FakeSandbox()
    actual_store: FilesystemArtifactStore | None = (
        FilesystemArtifactStore(settings.artifact_root) if store is _UNSET else store
    )
    run_kwargs: dict[str, Any] = {
        "title": "周报",
        "goal": "生成周报",
        "status": "pending",
        "skill_id": "team.weekly-report",
        "skill_object_key": PACKAGE_KEY,
        "skill_sha256": hashlib.sha256(package).hexdigest(),
        "skill_entrypoint": list(entrypoint),
        "skill_runtime": runtime,
        "skill_network": network,
        "skill_timeout_seconds": timeout_seconds,
    }
    run_kwargs.update(run_overrides or {})
    async with session_scope(factory) as session:
        run = Run(tenant_id="t-1", **run_kwargs)
        session.add(run)
        await session.flush()
        run_id = run.id
    runner = SkillRunner(
        factory,
        sandbox=sandbox,
        workspace=workspace,
        store=actual_store,
        settings=settings,
    )
    env = Env(
        runner=runner,
        sandbox=sandbox,
        workspace=workspace,
        run_id=run_id,
        factory=factory,
        settings=settings,
        store=actual_store,
        package=package,
        run=run,
    )
    try:
        yield env
    finally:
        await env.close()
