"""本地文档库:目录受限的只读访问 + 工具层装配。"""

import json
from pathlib import Path

import pytest
from common.errors import StorageError

from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.tools import build_tools


@pytest.fixture()
def library(tmp_path: Path) -> DocumentLibrary:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "周报.md").write_text("# 周报\n本周要点", encoding="utf-8")
    (root / "sub" / "notes.txt").write_text("notes", encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG")
    return DocumentLibrary(root)


async def test_list_filters_and_sorts(library: DocumentLibrary) -> None:
    entries = await library.list()
    # 只列允许的后缀,按相对路径排序
    assert [entry.path for entry in entries] == ["sub/notes.txt", "周报.md"]
    assert entries[1].size_bytes == len("# 周报\n本周要点".encode())
    assert entries[1].modified_at


async def test_read_returns_content(library: DocumentLibrary) -> None:
    assert await library.read("周报.md") == "# 周报\n本周要点"
    assert await library.read("sub/notes.txt") == "notes"


@pytest.mark.parametrize(
    "evil", ["../outside.md", "/etc/passwd", "sub/../../outside.md"]
)
async def test_traversal_rejected(library: DocumentLibrary, evil: str) -> None:
    with pytest.raises(StorageError) as exc_info:
        await library.read(evil)
    assert exc_info.value.code == "document.invalid_path"


async def test_unsupported_suffix_rejected(library: DocumentLibrary) -> None:
    with pytest.raises(StorageError) as exc_info:
        await library.read("image.png")
    assert exc_info.value.code == "document.unsupported_type"


async def test_missing_document(library: DocumentLibrary) -> None:
    with pytest.raises(StorageError) as exc_info:
        await library.read("nope.md")
    assert exc_info.value.code == "document.not_found"


async def test_oversize_rejected(tmp_path: Path) -> None:
    root = tmp_path / "small"
    root.mkdir()
    (root / "big.md").write_text("x" * 100, encoding="utf-8")
    library = DocumentLibrary(root, max_bytes=10)

    with pytest.raises(StorageError) as exc_info:
        await library.read("big.md")
    assert exc_info.value.code == "document.too_large"


async def test_tools_expose_documents(library: DocumentLibrary) -> None:
    registry = build_tools("t-1", documents=library)
    assert set(registry.names) == {"list_documents", "read_document"}
    assert all(registry.requires_confirmation(name) is False for name in registry.names)

    listed = json.loads(await registry.execute("list_documents", "{}"))
    assert [entry["path"] for entry in listed["documents"]] == [
        "sub/notes.txt",
        "周报.md",
    ]

    read = json.loads(await registry.execute("read_document", '{"path": "周报.md"}'))
    assert read["content"] == "# 周报\n本周要点"
    assert read["truncated"] is False

    missing_arg = json.loads(await registry.execute("read_document", "{}"))
    # P1-G §6.5:执行前统一校验参数,错误文本来自 common.tools.validate_arguments
    assert "工具参数无效" in missing_arg["error"]
    assert "path" in missing_arg["error"]

    # 越界读取在工具层同样收敛为错误文本(不炸图)
    evil = json.loads(await registry.execute("read_document", '{"path": "../x.md"}'))
    assert "error" in evil
