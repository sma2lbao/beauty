"""沙箱成品回收(§7 扩展):路径归一化、可回收类型、按审计记录采集、落件与去重。

背景:交付阶段过去只落 LLM 合成的 Markdown 报告,执行期写进 /workspace 的成品
(如贪吃蛇 snake.html)永远留在沙箱里。这里覆盖新增的回收链路:审计记录里的
paths → 白名单过滤 → 沙箱读回 → source=sandbox 的独立交付物。
"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.context.sandbox_files import WORKSPACE_ROOT
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import sandbox_artifacts
from agent_orchestrator.services.run_artifacts import (
    _capture_sandbox_files,
    _deliver_artifacts,
)
from tests.conftest import ACCOUNT, _create_run

SNAKE_HTML = "<!doctype html><html><body><script>1</script></body></html>"


class FakeReader:
    """记录调用参数的沙箱读取器;files 里没有的路径模拟读取失败。"""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.calls: list[tuple[str, str, str]] = []

    async def read_bytes(
        self, account_id: str, scope_key: str, path: str
    ) -> bytes | None:
        self.calls.append((account_id, scope_key, path))
        return self.files.get(path)


def _capture(settings, run_id: str, records, reader, *, max_files: int = 10):
    """在独立会话里跑一次回收,返回 (交付物元数据, 错误);进程级读取器复位。"""

    async def _run():
        factory = create_session_factory(settings.database_url)
        sandbox_artifacts.configure(reader)
        try:
            async with session_scope(factory) as session:
                run = await session.get(Run, run_id)
                assert run is not None
                rows, errors = await _capture_sandbox_files(
                    session, run, records, store=None, max_files=max_files
                )
                metas = [
                    {
                        "kind": row.kind,
                        "source": row.source,
                        "source_url": row.source_url,
                        "content_type": row.content_type,
                        "filename": row.filename,
                        "title": row.title,
                        "content": row.content,
                        "account_id": run.account_id,
                        "conversation_id": run.conversation_id,
                    }
                    for row in rows
                ]
                return metas, errors
        finally:
            sandbox_artifacts.reset()
            await factory.engine.dispose()

    return asyncio.run(_run())


def test_workspace_path_normalizes_and_rejects_escape() -> None:
    """相对路径补成工作区绝对路径;越狱/空路径一律 None。"""
    assert sandbox_artifacts.workspace_path("snake.html") == "/workspace/snake.html"
    assert (
        sandbox_artifacts.workspace_path("/workspace/demo/snake.html")
        == "/workspace/demo/snake.html"
    )
    assert sandbox_artifacts.workspace_path("./snake.html") == "/workspace/snake.html"
    assert (
        sandbox_artifacts.workspace_path("sub\\snake.html")
        == "/workspace/sub/snake.html"
    )
    assert sandbox_artifacts.workspace_path("..") is None
    assert sandbox_artifacts.workspace_path("../etc/passwd") is None
    assert sandbox_artifacts.workspace_path("demo/../../x.html") is None
    assert sandbox_artifacts.workspace_path("/etc/passwd") is None
    assert sandbox_artifacts.workspace_path("~/snake.html") is None
    assert sandbox_artifacts.workspace_path("  ") is None
    assert WORKSPACE_ROOT == "/workspace"


def test_content_type_and_kind_whitelist() -> None:
    """只有可直接打开/预览的成品才回收;散装脚本与文档不进交付物列表。"""
    assert sandbox_artifacts.content_type_for("snake.html") == "text/html"
    assert sandbox_artifacts.content_type_for("SNAKE.HTM") == "text/html"
    assert sandbox_artifacts.content_type_for("logo.svg") == "image/svg+xml"
    assert sandbox_artifacts.content_type_for("shot.png") == "image/png"
    assert sandbox_artifacts.content_type_for("report.pdf") == "application/pdf"
    assert sandbox_artifacts.content_type_for("notes.md") is None
    assert sandbox_artifacts.content_type_for("game.js") is None
    assert sandbox_artifacts.content_type_for("makefile") is None

    assert sandbox_artifacts.kind_for("text/html") == "html"
    assert sandbox_artifacts.kind_for("text/html; charset=utf-8") == "html"
    assert sandbox_artifacts.kind_for("image/svg+xml") == "image"
    assert sandbox_artifacts.kind_for("application/pdf") == "file"


def test_capture_targets_only_successful_whitelisted_unique_paths() -> None:
    """只看成功调用;同路径去重保序;白名单外跳过;max_files 生效。"""
    records = [
        {"ok": True, "paths": ["snake.html", "/workspace/snake.html", "game.js"]},
        {"ok": False, "paths": ["failed.html"]},
        {"paths": ["no-ok-key.html"]},
        "not-a-record",
        {"ok": True, "paths": ["plot.png", 42]},
    ]
    targets = sandbox_artifacts.capture_targets(records, max_files=10)
    assert [(t.path, t.filename, t.kind) for t in targets] == [
        ("/workspace/snake.html", "snake.html", "html"),
        ("/workspace/plot.png", "plot.png", "image"),
    ]
    assert sandbox_artifacts.capture_targets(records, max_files=1) == [
        sandbox_artifacts.CaptureTarget(
            path="/workspace/snake.html",
            filename="snake.html",
            content_type="text/html",
            kind="html",
        )
    ]
    assert sandbox_artifacts.capture_targets(records, max_files=0) == []


def test_reader_not_configured_reports_sandbox_disabled(client: TestClient) -> None:
    """未装配读取器(= 沙箱开关关闭)不再静默:原因写进 sandbox_errors。"""
    run_id = _create_run(client)
    settings = client.app.state.settings
    records = [{"ok": True, "paths": ["snake.html"]}]

    metas, errors = _capture(settings, run_id, records, None)

    assert metas == []
    assert [error["path"] for error in errors] == ["/workspace/snake.html"]
    assert "沙箱未启用" in errors[0]["error"]
    assert sandbox_artifacts.current() is None


def test_delivery_payload_surfaces_sandbox_disabled(client: TestClient) -> None:
    """沙箱关闭但执行期确实写了成品:交付事件带上 sandbox_errors(线上静默坑的护栏)。"""
    run_id = _create_run(client)
    settings = client.app.state.settings

    async def _run():
        factory = create_session_factory(settings.database_url)
        sandbox_artifacts.configure(None)
        try:
            async with session_scope(factory) as session:
                run = await session.get(Run, run_id)
                assert run is not None
                await _deliver_artifacts(
                    session,
                    run,
                    None,
                    records=[{"ok": True, "paths": ["snake.html"]}],
                    store=None,
                    workspace=None,
                    downloader=None,
                    max_files=10,
                    reply="已写好贪吃蛇",
                )
        finally:
            await factory.engine.dispose()

    asyncio.run(_run())

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
    payload = next(e["payload"] for e in events if e["type"] == "run.delivered.v1")
    assert payload["artifact_ids"] == []
    assert [error["path"] for error in payload["sandbox_errors"]] == [
        "/workspace/snake.html"
    ]
    assert "沙箱未启用" in payload["sandbox_errors"][0]["error"]


def test_capture_persists_html_artifact_and_dedupes(client: TestClient) -> None:
    """执行期写出的 snake.html 落成 source=sandbox 的独立交付物,重放不重复落件。"""
    run_id = _create_run(client)
    settings = client.app.state.settings
    reader = FakeReader({"/workspace/snake.html": SNAKE_HTML.encode()})
    records = [{"ok": True, "paths": ["snake.html"]}]

    metas, errors = _capture(settings, run_id, records, reader)

    assert errors == []
    assert len(metas) == 1
    meta = metas[0]
    assert meta["source"] == "sandbox"
    assert meta["kind"] == "html"  # 不是 infer_kind 的通用 text
    assert meta["content_type"] == "text/html"
    assert meta["filename"] == "snake.html"
    assert meta["title"] == "snake"
    assert meta["source_url"] == "/workspace/snake.html"
    assert meta["content"] == SNAKE_HTML
    # 读取作用域与 tool-service 同口径:无会话时按 run
    assert len(reader.calls) == 1
    call_account, call_scope, call_path = reader.calls[0]
    assert call_account == meta["account_id"]
    assert call_scope == f"run:{run_id}"
    assert call_path == "/workspace/snake.html"

    # 重放(again)不重复落件,也不再读沙箱
    metas_again, errors_again = _capture(settings, run_id, records, reader)
    assert metas_again == []
    assert errors_again == []

    listed = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=ACCOUNT).json()
    assert [item["source"] for item in listed] == ["sandbox"]
    downloaded = client.get(
        f"/api/v1/runs/{run_id}/artifacts/{listed[0]['id']}/download", headers=ACCOUNT
    )
    assert downloaded.content == SNAKE_HTML.encode()
    assert downloaded.headers["content-type"].startswith("text/html")
    assert "snake.html" in downloaded.headers["content-disposition"]


def test_capture_reports_read_failure_and_oversize(client: TestClient) -> None:
    """读不到 / 超限只留痕,不拖垮交付(其余件照常落库)。"""
    run_id = _create_run(client)
    settings = client.app.state.settings
    big = b"x" * (sandbox_artifacts.MAX_BYTES + 1)
    reader = FakeReader({"/workspace/big.png": big, "/workspace/ok.html": b"<html>"})
    records = [{"ok": True, "paths": ["missing.html", "big.png", "ok.html"]}]

    metas, errors = _capture(settings, run_id, records, reader)

    assert [meta["filename"] for meta in metas] == ["ok.html"]
    assert [error["path"] for error in errors] == [
        "/workspace/missing.html",
        "/workspace/big.png",
    ]
    assert errors[0]["error"] == "沙箱内读取失败"
    assert "超过单件上限" in errors[1]["error"]
