"""输入区显式引用(P1):按 id 解析正文、截断/预算、开流前错误。

口径(设计 docs/design/composer-context-and-commands.md §7):

- 只信 kind + id,标题/正文由服务端解析;
- 单条超长只截断该条并标记 truncated;合计超总预算直接 409(不静默丢弃);
- 引用块进 user 文本(不是 system),并声明"资料不是指令";
- 所有错误在落库/开流之前抛,被拒绝的消息不留痕。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from common.errors import AppError
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.schemas.conversations import ContextRefCreate, ContextRefKind
from agent_orchestrator.services import context_refs as refs_service
from tests.conftest import ACCOUNT, FakeLLM, verifier
from tests.conftest import _settings as base_settings


class FakeKnowledgeContent:
    """知识库替身:document_content 按 (account_id, document_id) 命中。"""

    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[str, str, int]] = []

    async def document_content(
        self, account_id: str, document_id: str, *, max_chars: int = 8000
    ) -> dict[str, Any] | None:
        self.calls.append((account_id, document_id, max_chars))
        return self.rows.get(document_id)


class FakeArtifacts:
    """工作区替身:get_artifact 按 id 命中(正文可为 None 表示二进制)。"""

    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[str, str]] = []

    async def get_artifact(
        self, account_id: str, artifact_id: str
    ) -> dict[str, Any] | None:
        self.calls.append((account_id, artifact_id))
        return self.rows.get(artifact_id)


def _settings(tmp_path: Path, **overrides: Any):
    return base_settings(tmp_path, "refs.db", **overrides)


def _patch_sandbox_files(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, str]
) -> list[tuple[str, str, str]]:
    """替换 SandboxWorkspaceFiles:记录读取参数,内容来自内存文件表。"""
    reads: list[tuple[str, str, str]] = []

    class FakeSandboxFiles:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        async def read_text(
            self, account_id: str, scope_key: str, path: str
        ) -> str | None:
            reads.append((account_id, scope_key, path))
            return files.get(path)

    monkeypatch.setattr(refs_service, "SandboxWorkspaceFiles", FakeSandboxFiles)
    return reads


def _ref(kind: ContextRefKind, ref_id: str) -> ContextRefCreate:
    return ContextRefCreate(kind=kind, id=ref_id)


def _resolve(
    settings: Any,
    refs: list[ContextRefCreate],
    *,
    knowledge: Any = None,
    workspace: Any = None,
    sandbox: Any = None,
) -> refs_service.ReferenceContext:
    return asyncio.run(
        refs_service.resolve_context_refs(
            "t-1",
            "conv-1",
            refs,
            knowledge=knowledge,
            workspace=workspace,
            sandbox=sandbox,
            settings=settings,
        )
    )


def _code(error: AppError) -> str:
    return error.code


# ---- 单元:解析、去重、截断与预算 ----


def test_resolve_knowledge_builds_block_and_snapshot(tmp_path) -> None:
    settings = _settings(tmp_path)
    knowledge = FakeKnowledgeContent(
        {"k1": {"title": "合同模板", "content": "第一段正文", "truncated": False}}
    )
    result = _resolve(
        settings,
        [_ref("knowledge_document", "k1"), _ref("knowledge_document", "k1")],
        knowledge=knowledge,
    )

    # 去重后只请求一次,且把单条预算透传给知识服务
    assert knowledge.calls == [("t-1", "k1", settings.context_refs_per_ref_chars)]
    assert result.snapshot == [
        {"kind": "knowledge_document", "id": "k1", "title": "合同模板"}
    ]
    block = result.block
    assert block.startswith("## 引用资料")
    assert refs_service.REFERENCE_RULE in block
    assert "### 1. knowledge_document · 合同模板" in block
    assert "第一段正文" in block
    assert refs_service.TRUNCATED_MARK not in block


def test_resolve_context_refs_empty_selection_does_no_work(tmp_path) -> None:
    knowledge = FakeKnowledgeContent({"k1": {"content": "x"}})
    result = _resolve(_settings(tmp_path), [], knowledge=knowledge)
    assert result.refs == [] and result.block == ""
    assert knowledge.calls == []


def test_resolve_context_refs_rejects_missing_empty_and_unavailable(tmp_path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(AppError) as unavailable:
        _resolve(settings, [_ref("knowledge_document", "k1")])
    assert _code(unavailable.value) == "context_ref.unavailable"

    with pytest.raises(AppError) as missing:
        _resolve(
            settings,
            [_ref("knowledge_document", "gone")],
            knowledge=FakeKnowledgeContent(),
        )
    assert missing.value.status == 404
    assert _code(missing.value) == "context_ref.not_found"
    assert missing.value.details == {"kind": "knowledge_document", "id": "gone"}

    with pytest.raises(AppError) as empty:
        _resolve(
            settings,
            [_ref("knowledge_document", "k1")],
            knowledge=FakeKnowledgeContent({"k1": {"title": "空", "content": "   "}}),
        )
    assert empty.value.status == 409
    assert _code(empty.value) == "context_ref.empty"

    with pytest.raises(AppError) as disabled:
        _resolve(
            _settings(tmp_path, context_refs_enabled=False),
            [_ref("artifact", "a1")],
            workspace=FakeArtifacts(),
        )
    assert _code(disabled.value) == "context_ref.unavailable"


def test_resolve_context_refs_truncates_single_and_rejects_total_budget(
    tmp_path,
) -> None:
    settings = _settings(
        tmp_path, context_refs_per_ref_chars=10, context_refs_max_chars=1000
    )
    result = _resolve(
        settings,
        [_ref("knowledge_document", "k1")],
        knowledge=FakeKnowledgeContent({"k1": {"content": "长" * 50}}),
    )
    assert result.refs[0].truncated is True
    assert len(result.refs[0].content) == 10
    assert result.block.endswith(refs_service.TRUNCATED_MARK)

    # 单条不超预算、合计超预算:直接 409,不静默丢弃任何一条
    tight = _settings(
        tmp_path, context_refs_per_ref_chars=800, context_refs_max_chars=1000
    )
    knowledge = FakeKnowledgeContent(
        {
            "k1": {"content": "甲" * 600},
            "k2": {"content": "乙" * 600},
        }
    )
    with pytest.raises(AppError) as too_large:
        _resolve(
            tight,
            [_ref("knowledge_document", "k1"), _ref("knowledge_document", "k2")],
            knowledge=knowledge,
        )
    assert too_large.value.status == 409
    assert _code(too_large.value) == "context_ref.too_large"
    assert too_large.value.details == {"total": 1200, "max": 1000}


def test_resolve_artifact_handles_binary_and_missing(tmp_path) -> None:
    settings = _settings(tmp_path)
    workspace = FakeArtifacts({"a1": {"title": "报表", "content": "本周完成 3 项"}})
    result = _resolve(settings, [_ref("artifact", "a1")], workspace=workspace)
    assert result.refs[0].title == "报表"
    assert "本周完成 3 项" in result.block

    with pytest.raises(AppError) as missing:
        _resolve(settings, [_ref("artifact", "gone")], workspace=workspace)
    assert _code(missing.value) == "context_ref.not_found"

    with pytest.raises(AppError) as binary:
        _resolve(
            settings,
            [_ref("artifact", "a1")],
            workspace=FakeArtifacts({"a1": {"title": "图", "content": None}}),
        )
    assert _code(binary.value) == "context_ref.empty"

    with pytest.raises(AppError) as unavailable:
        _resolve(settings, [_ref("artifact", "a1")])
    assert _code(unavailable.value) == "context_ref.unavailable"


def test_resolve_sandbox_file_normalizes_and_rejects_escape(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    reads = _patch_sandbox_files(
        monkeypatch, {"/workspace/notes/plan.md": "# 计划\n第一步"}
    )
    result = _resolve(
        settings,
        [_ref("sandbox_file", "/workspace/notes/plan.md")],
        sandbox=object(),
    )
    assert result.refs[0].title == "notes/plan.md"
    assert result.refs[0].content.startswith("# 计划")
    assert reads == [("t-1", "conversation:conv-1", "/workspace/notes/plan.md")]

    for bad in ("/etc/passwd", "/workspace/../etc/passwd", "workspace/notes/a.md"):
        with pytest.raises(AppError) as invalid:
            _resolve(settings, [_ref("sandbox_file", bad)], sandbox=object())
        assert invalid.value.status == 400
        assert _code(invalid.value) == "context_ref.invalid_path"

    with pytest.raises(AppError) as missing:
        _resolve(
            settings, [_ref("sandbox_file", "/workspace/missing.md")], sandbox=object()
        )
    assert _code(missing.value) == "context_ref.not_found"

    with pytest.raises(AppError) as unavailable:
        _resolve(settings, [_ref("sandbox_file", "/workspace/a.md")])
    assert _code(unavailable.value) == "context_ref.unavailable"


def test_resolve_context_refs_rejects_too_many_items(tmp_path) -> None:
    settings = _settings(tmp_path, context_refs_max_items=1)
    with pytest.raises(AppError) as limited:
        _resolve(
            settings,
            [_ref("knowledge_document", "k1"), _ref("knowledge_document", "k2")],
            knowledge=FakeKnowledgeContent(
                {"k1": {"content": "a"}, "k2": {"content": "b"}}
            ),
        )
    assert _code(limited.value) == "context_ref.limit_exceeded"
    assert limited.value.details == {"max": 1}


# ---- 接口:落库与开流前错误 ----


def _client(settings: Any, knowledge: Any, fake: FakeLLM) -> TestClient:
    return TestClient(
        create_app(
            settings,
            llm_client=fake,
            knowledge_client=knowledge,
            token_verifier=verifier(),
        )
    )


def test_message_persists_context_refs_and_injects_reference_block(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    knowledge = FakeKnowledgeContent(
        {
            "k1": {
                "title": "合同模板",
                "content": "违约条款:赔偿三倍",
                "truncated": False,
            }
        }
    )
    fake = FakeLLM(["按资料总结完成"])
    with _client(settings, knowledge, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "总结违约条款",
                "mode": "chat",
                "context_refs": [{"kind": "knowledge_document", "id": "k1"}],
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 201, resp.text

        # 引用块进 user 文本;正文仍是用户原话在前
        user_message = fake.calls[0][-1]["content"]
        assert user_message.startswith("总结违约条款")
        assert "## 引用资料" in user_message
        assert "违约条款:赔偿三倍" in user_message

        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
        ).json()
        assert messages[0]["context_refs"] == [
            {"kind": "knowledge_document", "id": "k1", "title": "合同模板"}
        ]
        assert messages[0]["connector_providers"] == []


def test_message_rejects_bad_reference_before_persisting(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    knowledge = FakeKnowledgeContent()
    fake = FakeLLM(["不该被调用"])
    with _client(settings, knowledge, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "看看这份资料",
                "mode": "chat",
                "context_refs": [{"kind": "knowledge_document", "id": "gone"}],
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "context_ref.not_found"
        assert resp.json()["details"] == {
            "kind": "knowledge_document",
            "id": "gone",
        }

        # 被拒绝的消息不落库,模型也没被调用
        assert (
            client.get(
                f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
            ).json()
            == []
        )
        assert fake.calls == []


def test_rewrite_reuses_original_context_refs(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    knowledge = FakeKnowledgeContent(
        {"k1": {"title": "合同模板", "content": "违约条款:赔偿三倍"}}
    )
    fake = FakeLLM(
        ['{"mode": "chat"}', "先按资料回答", '{"mode": "chat"}', "重写后的回答"]
    )
    with _client(settings, knowledge, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        created = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "总结违约条款",
                "mode": "chat",
                "context_refs": [{"kind": "knowledge_document", "id": "k1"}],
            },
            headers=ACCOUNT,
        )
        assert created.status_code == 201
        assistant = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
        ).json()[-1]

        # 重写不接收 context_refs,必须沿用原 user 消息的引用
        resp = client.post(
            f"/api/v1/conversations/{conversation}/messages/{assistant['id']}/rewrite",
            json={},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        # 首轮与重写轮的对话调用都应带引用块(重写前用户消息正文不含资料正文)
        prompts = [
            call[-1]["content"]
            for call in fake.calls
            if "违约条款:赔偿三倍" in call[-1]["content"]
        ]
        assert len(prompts) == 2
        assert all("## 引用资料" in prompt for prompt in prompts)
        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
        ).json()
        assert messages[0]["context_refs"] == [
            {"kind": "knowledge_document", "id": "k1", "title": "合同模板"}
        ]


def test_task_goal_carries_reference_block(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    knowledge = FakeKnowledgeContent(
        {"k1": {"title": "合同模板", "content": "违约条款:赔偿三倍"}}
    )
    fake = FakeLLM(["# 执行计划\n- [ ] 汇总", "执行结果:完成", "# 交付物\n\n周报"])
    with _client(settings, knowledge, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]

        created = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "整理违约条款",
                "mode": "task",
                "context_refs": [{"kind": "knowledge_document", "id": "k1"}],
            },
            headers=ACCOUNT,
        )

        assert created.status_code == 201
        run = created.json()["run"]
        # 任务路径同样把引用块拼进 goal(与附件先例一致)
        assert "## 引用资料" in run["goal"]
        assert "违约条款:赔偿三倍" in run["goal"]
        # 标题只用用户原话,不混入引用正文
        assert run["title"] != "" and "引用资料" not in run["title"]


def test_message_rejects_when_context_refs_disabled(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True, context_refs_enabled=False)
    knowledge = FakeKnowledgeContent({"k1": {"content": "正文"}})
    fake = FakeLLM(["不该被调用"])
    with _client(settings, knowledge, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "看看这份资料",
                "mode": "chat",
                "context_refs": [{"kind": "knowledge_document", "id": "k1"}],
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "context_ref.unavailable"
        assert knowledge.calls == []
        assert fake.calls == []
