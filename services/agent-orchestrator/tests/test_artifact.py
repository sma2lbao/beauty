"""交付物(§7):LLM 合成落库、桩拼装、取件、多件交付物清单/下载、租户隔离。"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.artifacts import persist_file_artifact
from tests.conftest import OTHER, TENANT, FakeLLM, _client, _create_run

# 最小 PNG 头 + 填充:只为断言字节往返,不参与解码
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _add_file_artifact(settings, run_id: str, **kwargs) -> str:
    """直接经 service 给某个 run 登记一件文件交付物(绕开工具/技能链路)。"""

    async def _run() -> str:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await session.get(Run, run_id)
                assert run is not None
                row = await persist_file_artifact(session, run, store=None, **kwargs)
                return row.id
        finally:
            await factory.engine.dispose()

    return asyncio.run(_run())


def test_llm_delivery_persisted_with_event(tmp_path) -> None:
    verify_pass = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
    delivery = "# 本周周报\n\n- 日程已整理\n"
    fake = FakeLLM(["# 计划", "执行结果:日程已整理", verify_pass, delivery])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["content"] == delivery
        assert artifact["kind"] == "markdown"
        assert artifact["title"] == "周报"
        assert artifact["run_id"] == run_id
        # 报告件的多件交付物元数据:可下载、来源是 report
        assert artifact["content_type"] == "text/markdown"
        assert artifact["source"] == "report"
        assert artifact["filename"] is None

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        assert [e["type"] for e in events] == [
            "run.created.v1",
            "run.planned.v1",
            "run.approved.v1",
            "run.delivered.v1",
            "run.completed.v1",
        ]
        delivered = next(e for e in events if e["type"] == "run.delivered.v1")
        assert delivered["payload"]["chars"] == len(delivery)
        assert delivered["payload"]["title"] == "周报"


def test_stub_delivery_assembles_artifact(client: TestClient) -> None:
    """无 LLM(桩路径):deliver 拼装交付物并标注桩来源,同样可取件。"""
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    status_code = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
    assert status_code["status"] == "completed"

    artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
    assert artifact["title"] == "周报"
    assert "# 周报" in artifact["content"]
    assert "## 执行结果" in artifact["content"]
    assert "阶段 A 桩" in artifact["content"]


def test_artifact_missing_and_tenant_isolation(client: TestClient) -> None:
    """未交付(仅创建)→ 404 artifact.not_found;跨租户取件不可见。"""
    run_id = _create_run(client)
    resp = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "artifact.not_found"

    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    other = OTHER
    assert (
        client.get(f"/api/v1/runs/{run_id}/artifact", headers=other).status_code == 404
    )


def test_failed_run_has_no_artifact(tmp_path) -> None:
    """failed 终态不产交付物:取件 404,事件流也无 delivered(诚实语义)。"""
    fail_json = '{"summary": "仍未达标", "verdicts": {"日程已整理": false}}'
    fake = FakeLLM(["# 计划", "执行", fail_json, "执行", fail_json, "执行", fail_json])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "failed"
        assert (
            client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).status_code
            == 404
        )
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        assert "run.delivered.v1" not in [e["type"] for e in events]


def test_artifact_not_found_details_uses_run_id(client: TestClient) -> None:
    """未交付时取件的 404 details 用 run_id(入口是 run;由仓储声明)。"""
    run_id = _create_run(client)
    response = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "artifact.not_found"
    assert body["details"] == {"run_id": run_id}


def test_run_not_found_details_key(client: TestClient) -> None:
    """run 404 的 details 键名是 run_id(由仓储 id_detail_key 声明,别改名)。"""
    response = client.get("/api/v1/runs/nope", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "run.not_found"
    assert body["details"] == {"run_id": "nope"}


def test_artifact_list_empty_before_delivery(client: TestClient) -> None:
    """未交付的 run:清单返回空列表(不是 404),旧取件端点仍是 404。"""
    run_id = _create_run(client)
    resp = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
    assert resp.status_code == 200
    assert resp.json() == []


def test_artifact_list_unknown_run_is_404(client: TestClient) -> None:
    """run 不存在:清单与下载都 404 run.not_found(先校验 run)。"""
    assert client.get("/api/v1/runs/nope/artifacts", headers=TENANT).status_code == 404
    resp = client.get("/api/v1/runs/nope/artifacts/whatever/download", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"


def test_multi_artifacts_list_and_main_artifact_unchanged(client: TestClient) -> None:
    """报告之外再登记文本+二进制件:清单齐、旧 /artifact 仍取报告。"""
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    settings = client.app.state.settings
    assert settings is not None

    png_id = _add_file_artifact(
        settings,
        run_id,
        filename="截图.png",
        content_type="image/png",
        data=PNG,
        title="运行截图",
        source="skill",
    )
    notes_id = _add_file_artifact(
        settings,
        run_id,
        filename="notes.md",
        content_type="text/markdown",
        data="# 笔记\n".encode(),
        source="mcp",
    )

    main = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()

    listed = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
    assert listed.status_code == 200
    metas = {item["id"]: item for item in listed.json()}
    assert set(metas) == {main["id"], png_id, notes_id}

    png_meta = metas[png_id]
    assert png_meta["kind"] == "image"
    assert png_meta["title"] == "运行截图"
    assert png_meta["filename"] == "截图.png"
    assert png_meta["content_type"] == "image/png"
    assert png_meta["size_bytes"] == len(PNG)
    assert png_meta["storage"] == "database"
    assert png_meta["source"] == "skill"
    assert png_meta["run_id"] == run_id
    assert "content" not in png_meta  # 清单只给元数据,不搬正文

    notes_meta = metas[notes_id]
    assert notes_meta["kind"] == "markdown"
    assert notes_meta["content_type"] == "text/markdown"
    assert notes_meta["source"] == "mcp"
    assert notes_meta["size_bytes"] == len("# 笔记\n".encode())

    # 主交付物(旧契约)仍是报告:多件不会改变旧客户端拿到的内容
    assert main["source"] == "report"
    assert "# 周报" in main["content"]


def test_download_text_and_binary_artifacts(client: TestClient) -> None:
    """下载端点:文本/二进制同一入口,Content-Type 与 Content-Disposition 正确。"""
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    settings = client.app.state.settings
    png_id = _add_file_artifact(
        settings,
        run_id,
        filename="截图.png",
        content_type="image/png",
        data=PNG,
        source="skill",
    )
    notes_id = _add_file_artifact(
        settings,
        run_id,
        filename="notes.md",
        content_type="text/markdown",
        data="# 笔记\n".encode(),
    )

    main = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
    report = client.get(
        f"/api/v1/runs/{run_id}/artifacts/{main['id']}/download", headers=TENANT
    )
    assert report.status_code == 200
    assert report.content == main["content"].encode("utf-8")
    assert report.headers["content-type"].startswith("text/markdown")
    assert report.headers["cache-control"] == "private, no-store"
    disposition = report.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "%E5%91%A8%E6%8A%A5.md" in disposition  # 周报.md(UTF-8 编码)

    png = client.get(
        f"/api/v1/runs/{run_id}/artifacts/{png_id}/download", headers=TENANT
    )
    assert png.status_code == 200
    assert png.content == PNG
    assert png.headers["content-type"] == "image/png"
    assert "%E6%88%AA%E5%9B%BE.png" in png.headers["content-disposition"]  # 截图.png

    notes = client.get(
        f"/api/v1/runs/{run_id}/artifacts/{notes_id}/download", headers=TENANT
    )
    assert notes.status_code == 200
    assert notes.content == "# 笔记\n".encode()
    assert notes.headers["content-type"].startswith("text/markdown")
    assert "notes.md" in notes.headers["content-disposition"]


def test_binary_only_run_main_artifact_is_meta_only(client: TestClient) -> None:
    """没有报告、只有二进制件:旧 /artifact 不报错,content 为空串并带 content_type。"""
    run_id = _create_run(client)
    settings = client.app.state.settings
    png_id = _add_file_artifact(
        settings,
        run_id,
        filename="shot.png",
        content_type="image/png",
        data=PNG,
        source="skill",
    )

    main = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
    assert main.status_code == 200
    body = main.json()
    assert body["id"] == png_id
    assert body["content"] == ""
    assert body["content_type"] == "image/png"
    assert body["kind"] == "image"
    assert body["size_bytes"] == len(PNG)

    download = client.get(
        f"/api/v1/runs/{run_id}/artifacts/{png_id}/download", headers=TENANT
    )
    assert download.content == PNG


def test_download_artifact_of_other_run_or_tenant_is_404(client: TestClient) -> None:
    """artifact 不属于该 run / 该租户 → 404 artifact.not_found(不泄漏存在性)。"""
    run_a = _create_run(client)
    run_b = _create_run(client)
    settings = client.app.state.settings
    png_id = _add_file_artifact(
        settings,
        run_a,
        filename="a.png",
        content_type="image/png",
        data=PNG,
        source="skill",
    )

    cross_run = client.get(
        f"/api/v1/runs/{run_b}/artifacts/{png_id}/download", headers=TENANT
    )
    assert cross_run.status_code == 404
    assert cross_run.json()["code"] == "artifact.not_found"
    assert cross_run.json()["details"] == {"run_id": run_b, "artifact_id": png_id}

    cross_tenant = client.get(
        f"/api/v1/runs/{run_a}/artifacts/{png_id}/download", headers=OTHER
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["code"] == "run.not_found"

    assert (
        client.get(f"/api/v1/runs/{run_a}/artifacts", headers=OTHER).status_code == 404
    )
