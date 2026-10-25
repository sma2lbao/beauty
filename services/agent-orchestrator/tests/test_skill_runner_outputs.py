"""P3-C 技能包执行:运行时选择、产物边界与交付物登记(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from database.session import session_scope

from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services import skill_runner
from agent_orchestrator.services.skill_runner import OUTPUT_ROOT
from tests.support.skill_runner import FakeSandbox, make_env


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
