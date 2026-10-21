"""交付物存储(§7 阶段 B):database/filesystem 后端、多件交付物(文本+二进制)、
对象丢失语义与下载名推导。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from common.errors import StorageError
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.repositories import ArtifactRepository
from agent_orchestrator.services.artifacts import (
    artifact_download_name,
    build_store,
    persist_artifact,
    persist_file_artifact,
    read_artifact,
    read_artifact_bytes,
    read_artifact_text,
)
from tests.conftest import TENANT, FakeLLM, _create_run, _settings, verifier

VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
DELIVERY = "# 本周周报\n\n日程已整理。\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _fake_llm() -> FakeLLM:
    return FakeLLM(["# 计划", "执行结果", VERIFY_PASS, DELIVERY])


def _filesystem_app(tmp_path: Path, fake: FakeLLM, root: Path):
    settings = _settings(
        tmp_path,
        "store.db",
        llm_enabled=True,
        artifact_store="filesystem",
        artifact_root=str(root),
    )
    return create_app(settings, llm_client=fake, token_verifier=verifier()), settings


def _complete_run(client: TestClient) -> str:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    response = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert response.json()["status"] == "completed"
    return run_id


async def _load_row(settings, run_id: str) -> RunArtifact | None:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            return await session.scalar(
                select(RunArtifact).where(RunArtifact.run_id == run_id)
            )
    finally:
        await factory.engine.dispose()


async def _load_artifact(settings, artifact_id: str) -> RunArtifact:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            row = await session.get(RunArtifact, artifact_id)
            assert row is not None
            return row
    finally:
        await factory.engine.dispose()


async def _find_by_source_url(settings, run_id: str, url: str) -> RunArtifact | None:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            return await ArtifactRepository(session).find_by_source_url(
                "t-1", run_id, url
            )
    finally:
        await factory.engine.dispose()


async def _list_for_run(settings, run_id: str) -> list[RunArtifact]:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            return await ArtifactRepository(session).list_for_run("t-1", run_id)
    finally:
        await factory.engine.dispose()


def _add_file(
    settings, run_id: str, store=None, *, pin_created_at=None, **kwargs
) -> str:
    """经 service 给 run 登记一件文件交付物(store=None 即 database 后端)。"""

    async def _run() -> str:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await session.get(Run, run_id)
                assert run is not None
                row = await persist_file_artifact(session, run, store=store, **kwargs)
                if pin_created_at is not None:
                    row.created_at = pin_created_at
                return row.id
        finally:
            await factory.engine.dispose()

    return asyncio.run(_run())


# ---------------------------------------------------------------- 报告(旧行为)


def test_filesystem_store_writes_object_and_serves_content(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    app, settings = _filesystem_app(tmp_path, _fake_llm(), root)
    with TestClient(app) as client:
        run_id = _complete_run(client)

        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["content"] == DELIVERY
        assert artifact["storage"] == "filesystem"
        assert artifact["size_bytes"] == len(DELIVERY.encode("utf-8"))

        # 对象真的落盘,且键按 租户/run/交付物 分层
        files = [path for path in root.rglob("*") if path.is_file()]
        assert len(files) == 1
        assert files[0].read_text() == DELIVERY
        assert files[0].relative_to(root).parts[0] == "t-1"
        assert files[0].relative_to(root).parts[1] == run_id

        # 事件流记录存储后端
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        delivered = next(e for e in events if e["type"] == "run.delivered.v1")
        assert delivered["payload"]["storage"] == "filesystem"


async def test_database_row_keeps_only_reference(tmp_path: Path) -> None:
    """filesystem 模式下,DB 行不再保存正文——只留 object_key 与元数据。"""
    root = tmp_path / "artifacts"
    app, settings = _filesystem_app(tmp_path, _fake_llm(), root)
    with TestClient(app) as client:
        run_id = _complete_run(client)

    row = await _load_row(settings, run_id)
    assert row is not None
    assert row.storage == "filesystem"
    assert row.content is None  # 正文只在对象存储里
    assert row.object_key and row.object_key.endswith(".md")
    assert row.size_bytes == len(DELIVERY.encode("utf-8"))


async def test_database_store_is_default_and_keeps_content(tmp_path: Path) -> None:
    """默认 database 后端:内容留在行里(零配置路径不受影响)。"""
    app = create_app(
        _settings(tmp_path, "db.db", llm_enabled=True),
        llm_client=_fake_llm(),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _complete_run(client)
        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["storage"] == "database"
        assert artifact["content"] == DELIVERY

    settings = _settings(tmp_path, "db.db", llm_enabled=True)
    row = await _load_row(settings, run_id)
    assert row is not None
    assert row.content == DELIVERY
    assert row.object_key is None


def test_missing_object_maps_to_500(tmp_path: Path) -> None:
    """对象被外部删除:取件报 500 artifact.object_missing(内部存储问题)。"""
    root = tmp_path / "artifacts"
    app, _ = _filesystem_app(tmp_path, _fake_llm(), root)
    with TestClient(app) as client:
        run_id = _complete_run(client)
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink()

        response = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
        assert response.status_code == 500
        assert response.json()["code"] == "artifact.object_missing"


def test_unknown_store_backend_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "x.db", artifact_store="s3-not-yet")
    with pytest.raises(StorageError) as exc_info:
        build_store(settings)
    assert exc_info.value.code == "artifact.unknown_store"


# ------------------------------------------------------- database 后端的二进制件


def test_database_store_keeps_binary_bytes_and_masks_content(
    tmp_path: Path,
) -> None:
    """database 后端:PNG 入 content_bytes,read_artifact 不抛错且 content 为空串。"""
    app = create_app(_settings(tmp_path, "bin.db"), token_verifier=verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        settings = client.app.state.settings
        artifact_id = _add_file(
            settings,
            run_id,
            filename="截图.png",
            content_type="image/png",
            data=PNG,
            title="运行截图",
            source="skill",
        )

        row = asyncio.run(_load_artifact(settings, artifact_id))
        assert row.storage == "database"
        assert row.content is None  # 文本列不塞二进制
        assert row.content_bytes == PNG
        assert row.filename == "截图.png"
        assert row.content_type == "image/png"
        assert row.kind == "image"
        assert row.source == "skill"
        assert row.size_bytes == len(PNG)

        # 字节往返无损;文本读模型按空正文展示;二进制没有文本
        assert asyncio.run(read_artifact_bytes(row, None)) == PNG
        assert asyncio.run(read_artifact_text(row, None)) is None
        view = asyncio.run(read_artifact(row, None))
        assert view.content == ""
        assert view.content_type == "image/png"
        assert view.filename == "截图.png"
        assert view.size_bytes == len(PNG)

        # 下载端点直接吐原始字节(二进制件的唯一入口)
        download = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact_id}/download", headers=TENANT
        )
        assert download.status_code == 200
        assert download.content == PNG
        assert download.headers["content-type"] == "image/png"

        # 二进制件不进旧文本取件的正文
        main = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert main["content"] == ""
        assert main["content_type"] == "image/png"


def test_database_store_text_file_artifact_uses_content(tmp_path: Path) -> None:
    """文本类文件交付物(database 后端)仍入 content 列,可直接按文本读。"""
    app = create_app(_settings(tmp_path, "txt.db"), token_verifier=verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        settings = client.app.state.settings
        notes = "# 会议笔记\n\n- 结论 A\n"
        artifact_id = _add_file(
            settings,
            run_id,
            filename="notes.md",
            content_type="text/markdown",
            data=notes.encode(),
            source="mcp",
        )

        row = asyncio.run(_load_artifact(settings, artifact_id))
        assert row.content == notes
        assert row.content_bytes is None
        assert row.kind == "markdown"
        assert asyncio.run(read_artifact_text(row, None)) == notes

        downloaded = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact_id}/download", headers=TENANT
        )
        assert downloaded.content == notes.encode()
        assert downloaded.headers["content-type"].startswith("text/markdown")
        assert "notes.md" in downloaded.headers["content-disposition"]


# ------------------------------------------------------ filesystem 后端的文件件


def test_filesystem_store_writes_file_artifact_object(tmp_path: Path) -> None:
    """filesystem 后端:文件件落盘(按租户/run/交付物分层),DB 只留引用。"""
    root = tmp_path / "artifacts"
    app, settings = _filesystem_app(tmp_path, _fake_llm(), root)
    store = build_store(settings)
    assert store is not None
    with TestClient(app) as client:
        run_id = _create_run(client)
        artifact_id = _add_file(
            settings,
            run_id,
            store=store,
            filename="shot.png",
            content_type="image/png",
            data=PNG,
            source="skill",
        )

        files = [path for path in root.rglob("*") if path.is_file()]
        assert len(files) == 1
        assert files[0].read_bytes() == PNG
        relative = files[0].relative_to(root)
        assert relative.parts[0] == "t-1"
        assert relative.parts[1] == run_id
        assert files[0].suffix == ".png"

        row = asyncio.run(_load_artifact(settings, artifact_id))
        assert row.storage == "filesystem"
        assert row.content is None
        assert row.content_bytes is None
        assert row.object_key and row.object_key.endswith(".png")

        # 通过配置的 store 读回字节;API 下载走 app.state 的同一后端
        assert asyncio.run(read_artifact_bytes(row, store)) == PNG
        download = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact_id}/download", headers=TENANT
        )
        assert download.status_code == 200
        assert download.content == PNG


# ------------------------------------------------------- 列表顺序 / 文件元数据


def test_list_for_run_orders_by_created_at_then_id(tmp_path: Path) -> None:
    """清单顺序 = created_at 升序,同一时刻按 id 升序(API 与仓储一致)。"""
    app = create_app(_settings(tmp_path, "order.db"), token_verifier=verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        settings = client.app.state.settings
        later = _add_file(
            settings,
            run_id,
            filename="later.txt",
            content_type="text/plain",
            data=b"later",
            source="skill",
            pin_created_at=datetime(2026, 1, 2, 0, 0, 0),
        )
        early_a = _add_file(
            settings,
            run_id,
            filename="a.txt",
            content_type="text/plain",
            data=b"a",
            source="skill",
            pin_created_at=datetime(2026, 1, 1, 0, 0, 0),
        )
        early_b = _add_file(
            settings,
            run_id,
            filename="b.txt",
            content_type="text/plain",
            data=b"b",
            source="skill",
            pin_created_at=datetime(2026, 1, 1, 0, 0, 0),
        )

        expected = sorted([early_a, early_b]) + [later]
        assert [row.id for row in asyncio.run(_list_for_run(settings, run_id))] == (
            expected
        )
        listed = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
        assert [item["id"] for item in listed.json()] == expected


def test_file_artifact_kind_inference_and_filename_sanitizing(
    tmp_path: Path,
) -> None:
    """kind 按 content_type 推导;文件名取 basename 并去控制字符;来源 URL 可查。"""
    app = create_app(_settings(tmp_path, "meta.db"), token_verifier=verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        settings = client.app.state.settings
        image_id = _add_file(
            settings,
            run_id,
            filename="../../evil\x01.png",
            content_type="image/png",
            data=PNG,
            source_url="https://example.com/shot.png",
        )
        zip_id = _add_file(
            settings,
            run_id,
            filename="pkg.zip",
            content_type="application/zip",
            data=b"PK\x03\x04",
            source="skill",
        )
        text_id = _add_file(
            settings,
            run_id,
            filename="a.txt",
            content_type="text/plain",
            data=b"hi",
        )

        image = asyncio.run(_load_artifact(settings, image_id))
        assert image.filename == "evil.png"  # 路径已剥离、控制字符已清
        assert image.title == "evil.png"  # 未传 title 时默认取文件名
        assert image.kind == "image"
        assert image.source == "mcp"  # 默认来源
        assert image.source_url == "https://example.com/shot.png"

        same_url = asyncio.run(
            _find_by_source_url(settings, run_id, "https://example.com/shot.png")
        )
        assert same_url is not None
        assert same_url.id == image_id

        zip_row = asyncio.run(_load_artifact(settings, zip_id))
        assert zip_row.kind == "file"
        assert zip_row.content_bytes == b"PK\x03\x04"
        assert zip_row.content is None

        text_row = asyncio.run(_load_artifact(settings, text_id))
        assert text_row.kind == "text"
        assert text_row.content == "hi"


async def _report_download_name(settings, run_id: str) -> str:
    """按旧报告路径落一行,返回下载名(filename 为空 → title + 扩展名)。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = await session.get(Run, run_id)
            assert run is not None
            row = await persist_artifact(
                session,
                run,
                {"kind": "markdown", "title": "周报", "content": "# 周"},
                None,
            )
            return artifact_download_name(row)
    finally:
        await factory.engine.dispose()


async def _insert_bare_binary(settings, run_id: str) -> str:
    """直接插一行无 filename 的二进制件(模拟旧数据/外部写入)。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            row = RunArtifact(
                tenant_id="t-1",
                run_id=run_id,
                kind="image",
                title="截图",
                content_type="image/png",
                source="skill",
                storage="database",
                content_bytes=PNG,
                size_bytes=len(PNG),
            )
            session.add(row)
            await session.flush()
            return artifact_download_name(row)
    finally:
        await factory.engine.dispose()


def test_artifact_download_name_prefers_filename_then_title(
    tmp_path: Path,
) -> None:
    """下载名:filename 优先,否则 title + 按 content_type 推扩展名。"""
    app = create_app(_settings(tmp_path, "name.db"), token_verifier=verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        settings = client.app.state.settings
        named_id = _add_file(
            settings,
            run_id,
            filename="报告 v2.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4",
            source="skill",
        )
        named = asyncio.run(_load_artifact(settings, named_id))
        assert artifact_download_name(named) == "报告 v2.pdf"

        # 报告行没有 filename:title 周报 + text/markdown → 周报.md
        assert asyncio.run(_report_download_name(settings, run_id)) == "周报.md"
        # 无 filename 的二进制行:按 content_type 推 .png
        assert asyncio.run(_insert_bare_binary(settings, run_id)) == "截图.png"
