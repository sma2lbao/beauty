"""沙箱工作区文件访问 + AGENTS.md 发现/注入单测(零网络)。

覆盖:

- `scope_key_for` 优先级(对话 > run > 租户)与 run_id 反推;
- `SandboxWorkspaceFiles` 的 ensure 缓存/参数、读写降级、find 的 argv/过滤/降级;
- `discover_agents_documents` 的排序/深度/数量预算/单文件截断/缺失跳过/开关;
- `AgentsMdProvider` 的 scope_key、渲染格式、总预算截断与无文档降级。

全部走内存假实现(FakeSandbox / FakeWorkspaceFiles),不连沙箱、不发网络。
"""

from __future__ import annotations

import pytest
from common.clients.sandbox import SandboxExecResult, SandboxInfo

from agent_orchestrator.context.agents import (
    BUDGET_NOTE,
    AgentsMdProvider,
    discover_agents_documents,
)
from agent_orchestrator.context.pipeline import ContextRequest
from agent_orchestrator.context.policy import ContextPolicy
from agent_orchestrator.context.sandbox_files import (
    SCOPE_CONVERSATION_PREFIX,
    SCOPE_RUN_PREFIX,
    SCOPE_TENANT_PREFIX,
    SandboxWorkspaceFiles,
    run_id_from_scope_key,
    scope_key_for,
)

TENANT = "t-1"
PREFIX = "/workspace/.agent/offload"


class FakeSandbox:
    """Sandbox 协议的假实现:内存文件表 + 固定 exec 结果(零网络)。"""

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        exec_stdout: str = "",
        exec_exit_code: int = 0,
        fail_read: bool = False,
        fail_write: bool = False,
        fail_exec: bool = False,
    ) -> None:
        self.files = dict(files or {})
        self.exec_stdout = exec_stdout
        self.exec_exit_code = exec_exit_code
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.fail_exec = fail_exec
        self.ensure_calls: list[dict[str, object]] = []
        self.reads: list[tuple[str, str, str]] = []
        self.writes: list[tuple[str, str, str, bytes]] = []
        self.execs: list[tuple[str, str, list[str]]] = []

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
        self.ensure_calls.append(
            {
                "tenant_id": tenant_id,
                "scope_key": scope_key,
                "ttl_seconds": ttl_seconds,
                "run_id": run_id,
            }
        )
        info = SandboxInfo(
            id=f"sbx-{len(self.ensure_calls)}", name=scope_key, status="running"
        )
        return info, True

    async def read_file(self, tenant_id: str, sandbox_id: str, path: str) -> bytes:
        self.reads.append((tenant_id, sandbox_id, path))
        if self.fail_read or path not in self.files:
            raise RuntimeError(f"读取失败: {path}")
        return self.files[path]

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None:
        self.writes.append((tenant_id, sandbox_id, path, data))
        if self.fail_write:
            raise RuntimeError("写入失败")
        self.files[path] = data

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
        self.execs.append((tenant_id, sandbox_id, list(argv)))
        if self.fail_exec:
            raise RuntimeError("执行失败")
        return SandboxExecResult(
            exit_code=self.exec_exit_code,
            stdout=self.exec_stdout,
            stderr="",
            duration_ms=1,
            truncated=False,
        )


class FakeWorkspaceFiles:
    """WorkspaceFiles 的内存假实现:记录调用,按沙箱路径存文本。

    `find` 故意返回**全部**已登记路径(不按名字/深度过滤):这样排序、深度、
    文件名过滤都由被测的 `discover_agents_documents` 自己负责,测试才有意义。
    """

    def __init__(
        self,
        contents: dict[str, str] | None = None,
        *,
        fail_read_paths: tuple[str, ...] = (),
        fail_write: bool = False,
        raise_read: bool = False,
    ) -> None:
        self.contents = dict(contents or {})
        self.fail_read_paths = set(fail_read_paths)
        self.fail_write = fail_write
        self.raise_read = raise_read
        self.reads: list[tuple[str, str, str]] = []
        self.writes: list[tuple[str, str, str, bytes]] = []
        self.finds: list[tuple[str, str, tuple[str, ...], int]] = []

    async def read_text(self, tenant_id: str, scope_key: str, path: str) -> str | None:
        self.reads.append((tenant_id, scope_key, path))
        if self.raise_read:
            raise RuntimeError("读取炸了")
        if path in self.fail_read_paths:
            return None
        return self.contents.get(path)

    async def write_bytes(
        self, tenant_id: str, scope_key: str, path: str, data: bytes
    ) -> None:
        self.writes.append((tenant_id, scope_key, path, data))
        if self.fail_write:
            raise RuntimeError("写入失败")
        self.contents[path] = data.decode("utf-8")

    async def find(
        self,
        tenant_id: str,
        scope_key: str,
        names: tuple[str, ...] | list[str],
        *,
        max_depth: int,
    ) -> list[str]:
        self.finds.append((tenant_id, scope_key, tuple(names), max_depth))
        return list(self.contents)


def _request(**overrides: object) -> ContextRequest:
    data: dict[str, object] = {"tenant_id": TENANT, "run_id": "run-1"}
    data.update(overrides)
    return ContextRequest(**data)  # type: ignore[arg-type]


# ---- sandbox_files: scope_key 规则 ----


def test_scope_key_priority() -> None:
    assert SCOPE_CONVERSATION_PREFIX == "conversation:"
    assert SCOPE_RUN_PREFIX == "run:"
    assert SCOPE_TENANT_PREFIX == "tenant:"
    assert scope_key_for(TENANT) == f"tenant:{TENANT}"
    assert scope_key_for(TENANT, run_id="r-1") == "run:r-1"
    assert (
        scope_key_for(TENANT, conversation_id="c-1", run_id="r-1") == "conversation:c-1"
    )


def test_run_id_from_scope_key() -> None:
    assert run_id_from_scope_key("run:r-9") == "r-9"
    assert run_id_from_scope_key("run:") is None
    assert run_id_from_scope_key("conversation:c-1") is None
    assert run_id_from_scope_key("tenant:t-1") is None


# ---- sandbox_files: SandboxWorkspaceFiles ----


async def test_sandbox_files_ensure_cached_per_scope() -> None:
    sandbox = FakeSandbox({"/workspace/AGENTS.md": b"root"})
    files = SandboxWorkspaceFiles(sandbox, ttl_seconds=120)
    first = await files.read_text(TENANT, "conversation:c-1", "/workspace/AGENTS.md")
    second = await files.read_text(TENANT, "conversation:c-1", "/workspace/AGENTS.md")
    assert first == "root"
    assert second == "root"
    assert len(sandbox.ensure_calls) == 1
    call = sandbox.ensure_calls[0]
    assert call["tenant_id"] == TENANT
    assert call["scope_key"] == "conversation:c-1"
    assert call["ttl_seconds"] == 120
    assert call["run_id"] is None


async def test_sandbox_files_run_scope_passes_run_id() -> None:
    sandbox = FakeSandbox()
    files = SandboxWorkspaceFiles(sandbox)
    await files.read_text(TENANT, "run:r-9", "/workspace/missing.md")
    assert sandbox.ensure_calls[0]["run_id"] == "r-9"


async def test_read_text_missing_or_failing_returns_none() -> None:
    files = SandboxWorkspaceFiles(FakeSandbox())
    assert await files.read_text(TENANT, "tenant:t-1", "/workspace/none.md") is None
    failing = SandboxWorkspaceFiles(FakeSandbox(fail_read=True))
    assert await failing.read_text(TENANT, "tenant:t-1", "/workspace/AGENTS.md") is None


async def test_write_bytes_creates_parent_directory_then_writes() -> None:
    sandbox = FakeSandbox()
    files = SandboxWorkspaceFiles(sandbox)
    path = f"{PREFIX}/r-1/read-abcd1234.txt"
    await files.write_bytes(TENANT, "run:r-1", path, b"hi")
    assert sandbox.execs == [(TENANT, "sbx-1", ["mkdir", "-p", f"{PREFIX}/r-1"])]
    assert sandbox.writes == [(TENANT, "sbx-1", path, b"hi")]
    assert sandbox.files[path] == b"hi"


async def test_write_bytes_skips_mkdir_at_workspace_root() -> None:
    sandbox = FakeSandbox()
    files = SandboxWorkspaceFiles(sandbox)
    await files.write_bytes(TENANT, "tenant:t-1", "/workspace/AGENTS.md", b"x")
    assert sandbox.execs == []
    assert sandbox.files["/workspace/AGENTS.md"] == b"x"


async def test_write_bytes_raises_when_mkdir_fails() -> None:
    files = SandboxWorkspaceFiles(FakeSandbox(exec_exit_code=1))
    with pytest.raises(RuntimeError, match="创建沙箱目录失败"):
        await files.write_bytes(TENANT, "run:r-1", f"{PREFIX}/r-1/a.txt", b"x")


async def test_find_builds_argv_and_filters_workspace_paths() -> None:
    stdout = "\n".join(
        [
            "/workspace/AGENTS.md",
            "/workspace/sub/AGENTS.md",
            "/workspace/sub/AGENTS.md",
            "/tmp/AGENTS.md",
            "/workspace/deep/a/b/AGENTS.md",
        ]
    )
    sandbox = FakeSandbox(exec_stdout=stdout)
    files = SandboxWorkspaceFiles(sandbox)
    paths = await files.find(TENANT, "run:r-1", ["AGENTS.md", "CLAUDE.md"], max_depth=1)
    assert paths == ["/workspace/AGENTS.md", "/workspace/sub/AGENTS.md"]
    argv = sandbox.execs[0][2]
    assert argv[:6] == ["find", "/workspace", "-maxdepth", "2", "-type", "f"]
    assert argv[6] == "("
    assert argv.count("-name") == 2
    assert "AGENTS.md" in argv and "CLAUDE.md" in argv
    assert argv[-2:] == [")", "-print"]


async def test_find_degrades_on_failure_or_empty_names() -> None:
    failing = SandboxWorkspaceFiles(FakeSandbox(fail_exec=True))
    assert await failing.find(TENANT, "tenant:t-1", ["AGENTS.md"], max_depth=2) == []
    nonzero = SandboxWorkspaceFiles(
        FakeSandbox(exec_stdout="/workspace/AGENTS.md", exec_exit_code=2)
    )
    assert await nonzero.find(TENANT, "tenant:t-1", ["AGENTS.md"], max_depth=2) == []
    blank = SandboxWorkspaceFiles(FakeSandbox(exec_stdout="/workspace/AGENTS.md"))
    assert await blank.find(TENANT, "tenant:t-1", ["  "], max_depth=2) == []
    assert blank.sandbox.ensure_calls == []


# ---- agents: discover_agents_documents ----


async def test_discover_orders_shallow_first_and_filters_scope_of_workspace() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/sub/AGENTS.md": "sub",
            "/workspace/AGENTS.md": "root",
            "/workspace/aaa/AGENTS.md": "aaa",
            "/workspace/aaa/b/AGENTS.md": "deep",
            "/workspace/aaa/CLAUDE.md": "other-name",
            "/tmp/AGENTS.md": "outside",
        }
    )
    documents = await discover_agents_documents(
        files,
        ContextPolicy(agents_md_max_depth=1),
        tenant_id=TENANT,
        scope_key="conversation:c-1",
    )
    assert [document.path for document in documents] == [
        "/workspace/AGENTS.md",
        "/workspace/aaa/AGENTS.md",
        "/workspace/sub/AGENTS.md",
    ]
    assert files.finds == [(TENANT, "conversation:c-1", ("AGENTS.md",), 1)]


async def test_discover_depth_zero_keeps_only_root_files() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/sub/AGENTS.md": "sub",
            "/workspace/AGENTS.md": "root",
        }
    )
    documents = await discover_agents_documents(
        files,
        ContextPolicy(agents_md_max_depth=0),
        tenant_id=TENANT,
        scope_key="tenant:t-1",
    )
    assert [document.path for document in documents] == ["/workspace/AGENTS.md"]


async def test_discover_applies_max_files_and_per_file_budget() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/AGENTS.md": "0123456789",
            "/workspace/a/AGENTS.md": "abcdef",
        }
    )
    documents = await discover_agents_documents(
        files,
        ContextPolicy(agents_md_max_files=1, agents_md_file_max_chars=4),
        tenant_id=TENANT,
        scope_key="tenant:t-1",
    )
    assert len(documents) == 1
    assert documents[0].path == "/workspace/AGENTS.md"
    assert documents[0].content == "0123"
    assert documents[0].original_chars == 10
    assert documents[0].truncated is True


async def test_discover_skips_missing_or_unreadable_documents() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/AGENTS.md": "root",
            "/workspace/sub/AGENTS.md": "sub",
        },
        fail_read_paths=("/workspace/AGENTS.md",),
    )
    documents = await discover_agents_documents(
        files, ContextPolicy(), tenant_id=TENANT, scope_key="tenant:t-1"
    )
    assert [document.path for document in documents] == ["/workspace/sub/AGENTS.md"]

    broken = FakeWorkspaceFiles({"/workspace/AGENTS.md": "root"}, raise_read=True)
    assert (
        await discover_agents_documents(
            broken, ContextPolicy(), tenant_id=TENANT, scope_key="tenant:t-1"
        )
        == []
    )


async def test_discover_disabled_returns_empty_without_io() -> None:
    files = FakeWorkspaceFiles({"/workspace/AGENTS.md": "root"})
    assert (
        await discover_agents_documents(
            files,
            ContextPolicy(agents_md_enabled=False),
            tenant_id=TENANT,
            scope_key="tenant:t-1",
        )
        == []
    )
    assert (
        await discover_agents_documents(
            files,
            ContextPolicy(agents_md_names=()),
            tenant_id=TENANT,
            scope_key="tenant:t-1",
        )
        == []
    )
    assert files.finds == []


# ---- agents: AgentsMdProvider ----


async def test_provider_renders_documents_and_source() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/AGENTS.md": "根规则",
            "/workspace/sub/AGENTS.md": "子规则",
        }
    )
    provider = AgentsMdProvider(files, ContextPolicy())
    section = await provider.build(_request(conversation_id="conv-1"))
    assert section is not None
    assert section.id == "agents_md"
    assert section.title == "工作区规则(AGENTS.md)"
    assert "### /workspace/AGENTS.md\n根规则" in section.content
    assert "### /workspace/sub/AGENTS.md\n子规则" in section.content
    assert section.source == "AGENTS.md,sub/AGENTS.md"
    assert section.truncated is False
    assert files.finds == [(TENANT, "conversation:conv-1", ("AGENTS.md",), 2)]


async def test_provider_returns_none_without_documents_or_when_disabled() -> None:
    empty = AgentsMdProvider(FakeWorkspaceFiles(), ContextPolicy())
    assert await empty.build(_request()) is None
    disabled = AgentsMdProvider(
        FakeWorkspaceFiles({"/workspace/AGENTS.md": "x"}),
        ContextPolicy(agents_md_enabled=False),
    )
    assert await disabled.build(_request()) is None


async def test_provider_marks_truncated_single_file() -> None:
    files = FakeWorkspaceFiles({"/workspace/AGENTS.md": "a" * 50})
    provider = AgentsMdProvider(files, ContextPolicy(agents_md_file_max_chars=10))
    section = await provider.build(_request())
    assert section is not None
    assert section.truncated is True
    assert "a" * 10 in section.content
    assert "a" * 11 not in section.content


async def test_provider_truncates_at_total_budget() -> None:
    files = FakeWorkspaceFiles(
        {
            "/workspace/AGENTS.md": "a" * 30,
            "/workspace/sub/AGENTS.md": "b" * 30,
        }
    )
    provider = AgentsMdProvider(
        files,
        ContextPolicy(agents_md_max_chars=100, agents_md_file_max_chars=200),
    )
    section = await provider.build(_request())
    assert section is not None
    assert section.truncated is True
    assert len(section.content) <= 100
    assert "### /workspace/AGENTS.md" in section.content
    assert "### /workspace/sub/AGENTS.md" not in section.content
    assert section.content.endswith(BUDGET_NOTE)


async def test_provider_tiny_budget_truncates_raw_content() -> None:
    files = FakeWorkspaceFiles({"/workspace/AGENTS.md": "a" * 50})
    provider = AgentsMdProvider(files, ContextPolicy(agents_md_max_chars=5))
    section = await provider.build(_request())
    assert section is not None
    assert len(section.content) == 5
    assert section.truncated is True


async def test_provider_zero_budget_yields_none() -> None:
    files = FakeWorkspaceFiles({"/workspace/AGENTS.md": "a" * 50})
    provider = AgentsMdProvider(files, ContextPolicy(agents_md_max_chars=0))
    assert await provider.build(_request()) is None
