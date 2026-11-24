"""平台 adapter:把 AO 链路与检索排序包装成统一 Observation。"""

from __future__ import annotations

import pytest
from evalkit.schema import Case as PlatformCase

from agent_orchestrator.evals.platform_adapter import (
    AgentOrchestratorAdapter,
    RetrievalQualityAdapter,
)
from agent_orchestrator.evals.retrieval_quality import GOLDEN_QUERIES


def platform_case(case_id: str = "eval/one", **input_data) -> PlatformCase:
    return PlatformCase(id=case_id, input=input_data, graders=())


def test_chat_case_returns_observation() -> None:
    adapter = AgentOrchestratorAdapter()
    observation = adapter.run(
        platform_case(
            "eval/chat",
            kind="chat",
            goal="你好,帮我看看今天该做什么",
            llm_script=["你好!我可以帮你整理本周日程。"],
        )
    )
    assert observation.error is None
    assert "整理本周日程" in observation.output
    assert observation.fields["run_status"] is None
    assert isinstance(observation.fields["tokens"], int)
    assert "run.created.v1" not in observation.events
    adapter.close()


def test_task_case_exposes_status_tools_and_deliverables() -> None:
    adapter = AgentOrchestratorAdapter()
    observation = adapter.run(
        platform_case(
            "eval/task",
            kind="task",
            goal="整理本周日程并发布周报文档",
            llm_script=[
                "# 执行计划\n- [ ] 检索日程\n- [ ] 发布说明",
                {
                    "tool_calls": [
                        {
                            "name": "external_publish",
                            "arguments": {"title": "周报"},
                        }
                    ]
                },
                "已发布周报。",
                "# 交付物\n\n周报已发布。",
            ],
            steps=[
                "create_run",
                "approve_plan",
                "confirm_all",
                "wait_terminal",
            ],
        )
    )
    fields = observation.fields
    assert fields["run_status"] == "completed"
    assert "external_publish" in observation.tool_calls
    assert "run.completed.v1" in observation.events
    assert fields["deliverables"] == 1
    assert fields["duration_ms"] >= 0
    assert observation.error is None


def test_task_case_propagates_schema_errors() -> None:
    adapter = AgentOrchestratorAdapter()
    with pytest.raises(ValueError, match="未知步骤"):
        adapter.run(
            platform_case(
                "eval/bad",
                kind="task",
                goal="x",
                steps=["not-a-step"],
            )
        )


def test_retrieval_metrics_for_single_query() -> None:
    adapter = RetrievalQualityAdapter({"variant": "bm25_mmr"})
    query = GOLDEN_QUERIES[0]
    observation = adapter.run(
        platform_case(
            "retrieval/q01",
            query=query.query,
            relevant=list(query.relevant),
        )
    )
    metrics = observation.fields["metrics"]
    assert observation.fields["variant"] == "bm25_mmr"
    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 1.0
    assert 0.0 <= metrics["redundancy"] <= 1.0
    assert set(observation.fields["ranked"]) >= set(query.relevant)
    assert observation.output == " ".join(observation.fields["ranked"])


def test_retrieval_variant_override() -> None:
    adapter = RetrievalQualityAdapter({"variant": "bm25_mmr"})
    query = GOLDEN_QUERIES[1]
    observation = adapter.run(
        platform_case(
            "retrieval/q02",
            query=query.query,
            relevant=list(query.relevant),
            variant="legacy",
        )
    )
    assert observation.fields["variant"] == "legacy"
    assert observation.fields["metrics"]["recall_at_k"] == 1.0


def test_retrieval_unknown_variant() -> None:
    with pytest.raises(ValueError, match="未知检索口径"):
        RetrievalQualityAdapter({"variant": "nope"})
    adapter = RetrievalQualityAdapter()
    with pytest.raises(ValueError, match="未知检索口径"):
        adapter.run(
            platform_case(
                "retrieval/bad",
                query="周报",
                relevant=["d1"],
                variant="nope",
            )
        )


def test_retrieval_rejects_bad_input() -> None:
    adapter = RetrievalQualityAdapter()
    with pytest.raises(ValueError, match="query"):
        adapter.run(platform_case("retrieval/a", relevant=["d1"]))
    with pytest.raises(ValueError, match="query"):
        adapter.run(
            platform_case("retrieval/b", query="  ", relevant=["d1"])
        )
    with pytest.raises(ValueError, match="relevant"):
        adapter.run(platform_case("retrieval/c", query="周报"))
    with pytest.raises(ValueError, match="relevant"):
        adapter.run(
            platform_case("retrieval/d", query="周报", relevant=[])
        )
