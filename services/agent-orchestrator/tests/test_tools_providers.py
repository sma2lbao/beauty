"""多提供方装配(§8):文档工具经完整执行图被调用与回喂。"""

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import build_tools
from tests.conftest import TENANT, FakeLLM, _create_run, _settings, verifier

VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


def _doc_app(tmp_path: Path, fake: FakeLLM, library: DocumentLibrary) -> TestClient:
    settings = _settings(tmp_path, "docs.db", llm_enabled=True, tools_enabled=True)
    return TestClient(
        create_app(
            settings,
            llm_client=fake,
            toolkit_factory=lambda tenant: build_tools(tenant, documents=library),
            token_verifier=verifier(),
        )
    )


def test_document_tool_flows_through_graph(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "周报.md").write_text("# 历史周报\n上周要点", encoding="utf-8")
    library = DocumentLibrary(root)

    read_call = LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="c1", name="read_document", arguments='{"path": "周报.md"}'),
        ),
    )
    fake = FakeLLM(["# 计划", read_call, "已读到历史周报", VERIFY_PASS, "# 交付物"])

    with _doc_app(tmp_path, fake, library) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        # 只暴露已配置的提供方;且文档正文经 tool 消息回喂模型
        assert {item["function"]["name"] for item in fake.tools_seen[1]} == {
            "list_documents",
            "read_document",
            "todo_write",
        }
        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert tool_messages and "上周要点" in tool_messages[0]["content"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["name"] == "read_document"
        assert tool_event["payload"]["ok"] is True
