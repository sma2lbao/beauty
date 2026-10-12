"""交付物存储(§7 阶段 B):filesystem 后端落盘、DB 只留引用、对象丢失语义。"""

from pathlib import Path

import pytest
from common.errors import StorageError
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import RunArtifact
from agent_orchestrator.services.artifacts import build_store
from tests.conftest import TENANT, FakeLLM, _create_run, _settings

VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
DELIVERY = "# 本周周报\n\n邮件已汇总。\n"


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
    return create_app(settings, llm_client=fake), settings


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
        _settings(tmp_path, "db.db", llm_enabled=True), llm_client=_fake_llm()
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
