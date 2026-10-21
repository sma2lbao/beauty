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

import asyncio
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from common.artifacts import FilesystemArtifactStore
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import ArtifactRepository, RunRepository
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services import skill_runner
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.sandbox import SandboxExecResult, SandboxInfo
from agent_orchestrator.services.skill_runner import (
    LIST_OUTPUTS_PATH,
    OUTPUT_ROOT,
    PACKAGE_PATH,
    SKILL_ROOT,
    UNPACK_PATH,
    UNPACK_PY,
    SkillRunner,
)
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import TENANT, FakeLLM, FakeWorkspace, _settings, verifier

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


# ---------------------------------------------------------------------------
# execute:成功链路
# ---------------------------------------------------------------------------


async def test_execute_runs_entrypoint_and_registers_artifact(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        raw = await env.runner.execute("t-1", env.run_id, "请生成周报")
        result = json.loads(raw)

        assert result["exit_code"] == 0
        assert result["stdout"] == "周报已生成"
        assert result["truncated"] is False
        assert [item["path"] for item in result["outputs"]] == ["report.md"]
        assert result["outputs"][0]["artifact_id"] == result["artifact_id"]
        assert "已登记" in result["summary"]

        # 沙箱:按 run 快照起一次性 skill 沙箱,network 取快照值
        created = env.sandbox.created[0]
        assert created["kind"] == "skill"
        assert created["image"] == env.settings.skill_python_image
        assert created["network"] == "deny"
        assert created["ttl_seconds"] == env.settings.skill_sandbox_ttl_seconds
        assert created["run_id"] == env.run_id

        # 取包 → 写包/解包脚本 → 沙箱内解包(sha 已校验)
        assert env.workspace.gets == [("t-1", PACKAGE_KEY)]
        assert env.sandbox.files[f"sb-1:{PACKAGE_PATH}"] == env.package
        assert env.sandbox.files[f"sb-1:{UNPACK_PATH}"] == UNPACK_PY.encode()
        unpack = env.sandbox.execs[0]
        assert unpack["argv"] == ["python3", UNPACK_PATH]
        assert unpack["cwd"] == "/workspace"
        assert json.loads(unpack["stdin"]) == {
            "zip": PACKAGE_PATH,
            "dest": SKILL_ROOT,
        }

        # entrypoint:runtime 解释器 + 快照 argv,cwd=技能根,stdin 传 input/goal/title
        entry = env.sandbox.execs[1]
        assert entry["argv"] == ["python", "scripts/run.py"]
        assert entry["cwd"] == SKILL_ROOT
        assert json.loads(entry["stdin"]) == {
            "input": "请生成周报",
            "goal": "生成周报",
            "title": "周报",
        }
        assert entry["timeout_seconds"] == 120 + 30

        # 产物:列表脚本用配置上限,README 被读回正文
        listing = env.sandbox.execs[2]
        assert listing["argv"] == [
            "python3",
            LIST_OUTPUTS_PATH,
            OUTPUT_ROOT,
            str(env.settings.skill_max_output_files),
        ]
        assert env.sandbox.reads == [f"{OUTPUT_ROOT}/report.md"]

        # 交付物登记:逐文件一件,filename/title=相对路径,source 标记技能来源
        row = await env.artifact()
        assert row is not None
        assert row.id == result["artifact_id"]
        assert row.title == "report.md"
        assert row.filename == "report.md"
        assert row.kind == "markdown"
        assert row.content_type == "text/markdown"
        assert row.source == "skill"
        assert row.storage == "filesystem"
        assert env.store is not None and row.object_key
        assert await env.store.get(row.object_key) == "# 报告".encode()

        assert env.sandbox.destroyed == ["sb-1"]


async def test_execute_without_package_returns_error(tmp_path: Path) -> None:
    async with make_env(tmp_path, run_overrides={"skill_object_key": None}) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "没有绑定可执行的技能包" in result["error"]
        assert env.sandbox.created == []
        assert env.workspace.gets == []


async def test_execute_sha_mismatch_destroys_sandbox(tmp_path: Path) -> None:
    async with make_env(tmp_path, run_overrides={"skill_sha256": "b" * 64}) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "sha256" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        assert env.sandbox.files == {}  # 校验失败不写包
        assert env.sandbox.execs == []


async def test_execute_unpack_failure_reports_reason(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.unpack = _exec(
        exit_code=2,
        stdout=json.dumps({"ok": False, "code": "path_invalid", "message": "非法路径"}),
    )
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "解包失败" in result["error"]
        assert "非法路径" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        # 解包失败不再执行 entrypoint
        assert [item["argv"][1] for item in env.sandbox.execs] == [UNPACK_PATH]


async def test_execute_entrypoint_nonzero_exit_is_reported(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.entry = _exec(exit_code=2, stdout="partial", stderr="脚本炸了")
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 2
        assert result["stdout"] == "partial"
        assert result["stderr"] == "脚本炸了"
        assert env.sandbox.destroyed == ["sb-1"]
        # 失败也照样登记已产出的文本(让用户能拿到半成品)
        assert result["artifact_id"]
        assert await env.artifact() is not None


async def test_execute_rejects_unsafe_snapshot_entrypoint(tmp_path: Path) -> None:
    """快照被改成 ../ 开头时拒绝执行(不把 DB 快照当作可信输入)。"""
    async with make_env(tmp_path, entrypoint=("../evil.py",)) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "路径穿越" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        # 解包已发生,destroy 一定会跑
        assert [item["argv"][1] for item in env.sandbox.execs] == [UNPACK_PATH]


async def test_execute_workspace_failure_returns_error(tmp_path: Path) -> None:
    workspace = FakeWorkspace(fail=True)
    async with make_env(tmp_path, workspace=workspace) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "技能包读取失败" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]


async def test_execute_missing_run_returns_error(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        result = json.loads(await env.runner.execute("t-1", "no-such-run", None))
        assert "run 不存在" in result["error"]
        assert env.sandbox.created == []


async def test_execute_suppresses_destroy_failure(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.destroy_error = RuntimeError("沙箱已过期")
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 0
        assert env.sandbox.destroyed == ["sb-1"]


# ---------------------------------------------------------------------------
# execute:运行时选择、产物边界、交付物登记
# ---------------------------------------------------------------------------


async def test_node_runtime_uses_node_image_and_keeps_interpreter(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    async with make_env(
        tmp_path,
        sandbox=sandbox,
        entrypoint=("node", "scripts/run.js"),
        runtime="node",
    ) as env:
        await env.runner.execute("t-1", env.run_id, None)
        assert env.sandbox.created[0]["image"] == env.settings.skill_node_image
        # entrypoint 首段已是解释器:原样执行
        assert env.sandbox.execs[1]["argv"] == ["node", "scripts/run.js"]


async def test_python_entrypoint_without_interpreter_gets_runtime_bin(
    tmp_path: Path,
) -> None:
    async with make_env(tmp_path, entrypoint=("scripts/run.py",)) as env:
        await env.runner.execute("t-1", env.run_id, None)
        assert env.sandbox.execs[1]["argv"] == ["python3", "scripts/run.py"]


async def test_output_limits_and_binary_are_marked(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.listing = {
        "files": [
            {"path": "a.txt", "size": 3},
            {"path": "big.txt", "size": 999},
            {"path": "img.bin", "size": 3},
        ],
        "truncated": True,
    }
    sandbox.output_files = {"a.txt": b"abc", "img.bin": b"\xff\xfe\x00"}
    async with make_env(
        tmp_path,
        sandbox=sandbox,
        settings_overrides={
            "skill_max_output_files": 2,
            "skill_max_output_bytes": 3,
        },
    ) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        by_path = {item["path"]: item for item in result["outputs"]}
        assert by_path["a.txt"]["artifact_id"]
        assert by_path["big.txt"]["truncated"] is True  # 超限只报元数据
        assert by_path["img.bin"]["binary"] is True
        assert result["truncated"] is True
        # 超限文件不读正文
        assert env.sandbox.reads == [
            f"{OUTPUT_ROOT}/a.txt",
            f"{OUTPUT_ROOT}/img.bin",
        ]
        # 逐文件登记:文本与二进制各一件,超限文件(没读到字节)不登记
        rows = await env.artifacts()
        assert {row.title for row in rows} == {"a.txt", "img.bin"}
        by_title = {row.title: row for row in rows}
        assert by_title["a.txt"].content_type == "text/plain"
        assert by_title["a.txt"].source == "skill"
        assert by_title["img.bin"].kind == "file"
        assert by_title["img.bin"].content_type == "application/octet-stream"
        assert by_title["a.txt"].id == by_path["a.txt"]["artifact_id"]
        assert by_title["img.bin"].id == by_path["img.bin"]["artifact_id"]
        assert "artifact_id" not in by_path["big.txt"]
        assert env.store is not None
        assert await env.store.get(by_title["img.bin"].object_key) == b"\xff\xfe\x00"


async def test_multiple_outputs_registered_per_file(tmp_path: Path) -> None:
    """多文件逐件登记:每个文件一件交付物,各带自己的 artifact_id。"""
    sandbox = FakeSandbox()
    sandbox.listing = {
        "files": [
            {"path": "report.md", "size": 5},
            {"path": "notes.txt", "size": 3},
        ],
        "truncated": False,
    }
    sandbox.output_files = {"report.md": "# 报告".encode(), "notes.txt": b"abc"}
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        rows = await env.artifacts()
        assert {row.title for row in rows} == {"report.md", "notes.txt"}
        by_title = {row.title: row for row in rows}
        assert by_title["report.md"].kind == "markdown"
        assert by_title["report.md"].content_type == "text/markdown"
        assert by_title["notes.txt"].kind == "text"
        assert by_title["notes.txt"].content_type == "text/plain"
        assert env.store is not None
        contents = {row.title: await env.store.get(row.object_key) for row in rows}
        assert contents == {"report.md": "# 报告".encode(), "notes.txt": b"abc"}
        result_ids = {item["path"]: item["artifact_id"] for item in result["outputs"]}
        assert result_ids == {
            "report.md": by_title["report.md"].id,
            "notes.txt": by_title["notes.txt"].id,
        }
        assert len(set(result_ids.values())) == 2  # 不再合并成同一件
        assert result["artifact_id"] == by_title["report.md"].id
        assert "已登记 2 个文件" in result["summary"]


async def test_binary_outputs_are_registered(tmp_path: Path) -> None:
    """二进制产物不再被丢弃:落对象存储,content_type 按扩展名兜底。"""
    sandbox = FakeSandbox()
    sandbox.listing = {"files": [{"path": "blob.bin", "size": 2}], "truncated": False}
    sandbox.output_files = {"blob.bin": b"\xff\x00"}
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        rows = await env.artifacts()
        assert len(rows) == 1
        row = rows[0]
        assert row.filename == "blob.bin"
        assert row.kind == "file"
        assert row.content_type == "application/octet-stream"
        assert row.source == "skill"
        assert result["outputs"][0]["binary"] is True
        assert result["outputs"][0]["artifact_id"] == row.id
        assert env.store is not None
        assert await env.store.get(row.object_key) == b"\xff\x00"


async def test_listing_failure_does_not_fail_tool(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.listing_exit_code = 2
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 0
        assert result["outputs"] == []
        assert await env.artifacts() == []


async def test_read_failure_marks_single_output_truncated(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.read_error = RuntimeError("读不到")
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["outputs"][0]["truncated"] is True
        assert await env.artifacts() == []


async def test_artifact_persist_failure_does_not_fail_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("存储挂了")

    monkeypatch.setattr(skill_runner.artifacts, "persist_file_artifact", boom)
    async with make_env(tmp_path) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 0
        assert "产物登记失败" in result["summary"]
        assert "artifact_id" not in result
        assert all("artifact_id" not in item for item in result["outputs"])
        assert env.sandbox.destroyed == ["sb-1"]


async def test_database_store_keeps_content_in_row(tmp_path: Path) -> None:
    async with make_env(tmp_path, store=None) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        row = await env.artifact()
        assert row is not None and result["artifact_id"] == row.id
        assert row.storage == "database"
        assert row.object_key is None
        assert row.filename == "report.md"
        assert row.content_type == "text/markdown"
        assert row.content == "# 报告"


async def test_database_store_keeps_binary_in_content_bytes(tmp_path: Path) -> None:
    """database 模式:二进制件进 content_bytes(文本列为空),kind 按类型推导。"""
    sandbox = FakeSandbox()
    sandbox.listing = {"files": [{"path": "chart.png", "size": 4}], "truncated": False}
    sandbox.output_files = {"chart.png": b"\x89PNG"}
    async with make_env(tmp_path, sandbox=sandbox, store=None) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        row = await env.artifact()
        assert row is not None
        assert row.id == result["artifact_id"]
        assert row.storage == "database"
        assert row.content is None
        assert row.content_bytes == b"\x89PNG"
        assert row.content_type == "image/png"
        assert row.kind == "image"


async def test_delivery_adds_report_alongside_skill_outputs(tmp_path: Path) -> None:
    """交付阶段新增 report 件并成为主交付物;技能产物保留,不再被原地覆盖。"""
    async with make_env(tmp_path) as env:
        first = json.loads(await env.runner.execute("t-1", env.run_id, None))
        async with session_scope(env.factory) as session:
            run = await RunRepository(session).require_for_tenant("t-1", env.run_id)
            await runs_service._persist_artifact(
                session,
                run,
                {"title": "最终交付物", "kind": "markdown", "content": "# 最终"},
            )
        rows = await env.artifacts()
        assert len(rows) == 2  # 技能产物 + 报告各一件
        report = await env.artifact()  # 主交付物优先 report
        assert report is not None
        assert report.id != first["artifact_id"]
        assert report.title == "最终交付物"
        assert report.source == "report"
        skill_row = next(row for row in rows if row.id == first["artifact_id"])
        assert skill_row.source == "skill"
        assert skill_row.title == "report.md"


# ---------------------------------------------------------------------------
# 工具暴露(ToolSpec)与 handler 边界
# ---------------------------------------------------------------------------


async def test_spec_shape_and_confirmation(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        spec = env.runner.spec(env.run)
        assert spec.name == "skill_run"
        assert "沙箱" in spec.description
        assert "team.weekly-report" in spec.description
        # 沙箱内执行属受控写:自动执行,不再逐次人工确认
        assert spec.risk is ToolRisk.WRITE
        assert spec.requires_confirmation is False
        assert spec.parameters == {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "可选的输入文本,经 stdin JSON 传给脚本",
                }
            },
            "additionalProperties": False,
        }


async def test_spec_handler_returns_error_text_instead_of_raising(
    tmp_path: Path,
) -> None:
    workspace = FakeWorkspace(fail=True)
    async with make_env(tmp_path, workspace=workspace) as env:
        spec = env.runner.spec(env.run)
        output = json.loads(await spec.handler({"input": "x"}))
        assert "技能包读取失败" in output["error"]
        assert env.sandbox.destroyed == ["sb-1"]


async def test_spec_handler_passes_input_to_stdin(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        spec = env.runner.spec(env.run)
        output = json.loads(await spec.handler({"input": "来自模型"}))
        assert output["exit_code"] == 0
        assert json.loads(env.sandbox.execs[1]["stdin"])["input"] == "来自模型"


# ---------------------------------------------------------------------------
# UNPACK_PY 本地联调(真实子进程,零网络)
# ---------------------------------------------------------------------------


def _run_unpack(
    tmp_path: Path, data: bytes
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    script = tmp_path / "unpack.py"
    script.write_text(UNPACK_PY, encoding="utf-8")
    archive = tmp_path / "package.zip"
    archive.write_bytes(data)
    completed = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"zip": str(archive), "dest": str(tmp_path / "out")}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed, json.loads(completed.stdout)


def test_unpack_py_extracts_single_root_package(tmp_path: Path) -> None:
    data = build_zip(
        {
            "weekly/SKILL.md": SKILL_MD,
            "weekly/scripts/run.py": "print(1)",
        }
    )
    completed, payload = _run_unpack(tmp_path, data)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert (tmp_path / "out" / "SKILL.md").read_text() == SKILL_MD
    assert (tmp_path / "out" / "scripts" / "run.py").read_text() == "print(1)"


@pytest.mark.parametrize(
    ("files", "symlinks", "code"),
    [
        ({"SKILL.md": SKILL_MD, "../evil.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD, "/abs.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD, "C:/evil.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD}, {"link": "/etc/passwd"}, "path_invalid"),
    ],
)
def test_unpack_py_rejects_unsafe_paths(
    tmp_path: Path,
    files: dict[str, str],
    symlinks: dict[str, str] | None,
    code: str,
) -> None:
    completed, payload = _run_unpack(tmp_path, build_zip(files, symlinks=symlinks))
    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["code"] == code
    assert not (tmp_path / "evil.txt").exists()


def test_unpack_py_rejects_too_many_files(tmp_path: Path) -> None:
    files = {"SKILL.md": SKILL_MD}
    files.update({f"f{i}.txt": "x" for i in range(501)})
    completed, payload = _run_unpack(tmp_path, build_zip(files))
    assert completed.returncode == 2
    assert payload["code"] == "too_large"


# ---------------------------------------------------------------------------
# main 的 extras factory:skill_run 只在 run 绑定包时暴露
# ---------------------------------------------------------------------------


class HashingWorkspace(FakeWorkspace):
    """put_object 返回真实 sha256(与 run 快照一致),端到端执行要用。"""

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, Any]:
        self.sha256 = hashlib.sha256(data).hexdigest()
        return await super().put_object(
            tenant_id, data, filename=filename, content_type=content_type
        )


def _upload_package(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/skills/packages",
        files={"file": ("weekly.zip", plain_package(), "application/zip")},
        headers=TENANT,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_run(client: TestClient, **payload: Any) -> dict[str, Any]:
    response = client.post(
        "/api/v1/runs",
        json={"title": "周报", "goal": "生成周报", **payload},
        headers=TENANT,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _extras(client: TestClient, ctx: ToolContext) -> list[Any]:
    factory = client.app.state.tool_extras_factory
    with bind_tool_context(ctx):
        return asyncio.run(factory(ctx.tenant_id))


@pytest.fixture()
def skill_client(tmp_path: Path) -> Iterator[TestClient]:
    workspace = HashingWorkspace()
    sandbox = FakeSandbox()
    app = create_app(
        _settings(
            tmp_path, "extras.db", skill_execution_enabled=True, mcp_enabled=False
        ),
        workspace_client=workspace,
        sandbox_client=sandbox,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        yield client


def test_extras_exposes_skill_run_for_package_run(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    run = _create_run(skill_client, skill_id="team.weekly-report")
    specs = _extras(skill_client, ToolContext(tenant_id="t-1", run_id=run["id"]))
    assert [spec.name for spec in specs] == ["skill_run"]
    assert specs[0].risk is ToolRisk.WRITE
    assert specs[0].requires_confirmation is False


def test_extras_skips_skill_run_without_package(skill_client: TestClient) -> None:
    run = _create_run(skill_client)
    assert _extras(skill_client, ToolContext(tenant_id="t-1", run_id=run["id"])) == []


def test_extras_has_no_skill_run_in_chat_path(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    _create_run(skill_client, skill_id="team.weekly-report")
    ctx = ToolContext(tenant_id="t-1", conversation_id="conv-1")
    assert _extras(skill_client, ctx) == []


def test_extras_unknown_run_id_has_no_skill_run(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    ctx = ToolContext(tenant_id="t-1", run_id="no-such-run")
    assert _extras(skill_client, ctx) == []


def test_extras_mcp_failure_does_not_block_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("MCP 库挂了")

    monkeypatch.setattr(mcp_service, "tool_specs", boom)
    workspace = HashingWorkspace()
    app = create_app(
        _settings(tmp_path, "extras-mcp.db", skill_execution_enabled=True),
        workspace_client=workspace,
        sandbox_client=FakeSandbox(),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        _upload_package(client)
        run = _create_run(client, skill_id="team.weekly-report")
        specs = _extras(client, ToolContext(tenant_id="t-1", run_id=run["id"]))
    assert [spec.name for spec in specs] == ["skill_run"]


def test_runner_not_assembled_without_workspace(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path, "no-workspace.db", skill_execution_enabled=True),
        sandbox_client=FakeSandbox(),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        assert client.app.state.skill_runner is None


# ---------------------------------------------------------------------------
# 图路径端到端:skill_run 属受控写,approve 后自动执行
# ---------------------------------------------------------------------------


def test_graph_executes_skill_run_without_confirmation(tmp_path: Path) -> None:
    """skill_run 是受控写(WRITE):approve 后直接执行,不再弹确认闸(§6.2)。"""
    verify_pass = '{"summary": "已生成", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            LLMReply(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call_skill",
                        name="skill_run",
                        arguments='{"input": "本周日程"}',
                    ),
                ),
            ),
            "技能执行完成",
            verify_pass,
            "# 最终交付正文",
        ]
    )
    workspace = HashingWorkspace()
    sandbox = FakeSandbox()
    # 空产物目录:避免图事务持锁期间写产物(登记路径已由上面的单元用例覆盖)
    sandbox.listing = {"files": [], "truncated": False}
    app = create_app(
        _settings(
            tmp_path,
            "graph-skill.db",
            llm_enabled=True,
            skill_execution_enabled=True,
            mcp_enabled=False,
        ),
        llm_client=fake,
        workspace_client=workspace,
        sandbox_client=sandbox,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        assert client.app.state.skill_runner is not None
        _upload_package(client)
        run = _create_run(
            client,
            skill_id="team.weekly-report",
            acceptance_criteria=["日程已整理"],
        )

        planned = client.post(f"/api/v1/runs/{run['id']}/plan", headers=TENANT)
        assert planned.status_code == 200, planned.text
        assert sandbox.execs == []  # 审批前不执行上传代码

        done = client.post(f"/api/v1/runs/{run['id']}/approve", headers=TENANT)
        assert done.status_code == 200, done.text
        # 受控写自动执行:不再停在 awaiting_confirmation,直接跑完
        assert done.json()["status"] == "completed"

        # 沙箱生命周期:解包 → entrypoint → 列产物 → 销毁
        assert [item["argv"][1] for item in sandbox.execs][0] == UNPACK_PATH
        assert sandbox.destroyed == ["sb-1"]
        assert json.loads(sandbox.execs[1]["stdin"])["input"] == "本周日程"

        # execute 的 function-calling schema 里能看到 skill_run
        schema_names = [
            tools[0]["function"]["name"] for tools in fake.tools_seen if tools
        ]
        assert "skill_run" in schema_names

        artifact = client.get(
            f"/api/v1/runs/{run['id']}/artifact", headers=TENANT
        ).json()
        assert artifact["content"] == "# 最终交付正文"

        events = client.get(f"/api/v1/runs/{run['id']}/events", headers=TENANT).json()
        types = [event["type"] for event in events]
        # 没有确认闸事件:受控写不打断
        assert "run.confirmation.required.v1" not in types
        assert "run.confirmation.received.v1" not in types

        tool_events = [
            event for event in events if event["type"] == "run.tool.called.v1"
        ]
        assert [event["payload"]["name"] for event in tool_events] == ["skill_run"]
        assert tool_events[0]["payload"]["ok"] is True
