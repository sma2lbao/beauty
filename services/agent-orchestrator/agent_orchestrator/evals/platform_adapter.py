"""把 agent-orchestrator 与检索排序接入统一评测平台。

adapter 放在服务包内而不是 evalkit:平台核心不反向依赖具体服务,服务自己
负责把内部链路包装成 Observation。两条 adapter 都是进程内、零网络。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evalkit.graders import Observation
from evalkit.schema import Case as PlatformCase

from .retrieval_quality import (
    GOLDEN_DOCS,
    RANKERS,
    GoldenQuery,
    run_variant,
)
from .runner import run_case
from .suites import Case as OrchestratorCase


class AgentOrchestratorAdapter:
    """用现有 TestClient harness 驱动 chat/task 用例。"""

    name = "agent-orchestrator"

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})

    def run(self, case: PlatformCase) -> Observation:
        payload = {
            "id": case.id,
            "kind": case.input.get("kind", "task"),
            "goal": case.input.get("goal"),
            "llm_script": case.input.get("llm_script", []),
            "steps": case.input.get("steps", []),
            "policies": case.input.get("policies", []),
        }
        orchestrator_case = OrchestratorCase.from_dict(
            payload, where=f"平台用例 {case.id}"
        )
        result = run_case(case.group, orchestrator_case)
        fields = {
            "run_status": result.run_status,
            "tokens": result.tokens,
            "deliverables": result.deliverables,
            "duration_ms": round(result.duration_ms, 2),
            "events": list(result.events),
            "tool_calls": list(result.tool_calls),
        }
        error = "; ".join(result.failures) if result.failures else None
        return Observation(
            output=result.reply,
            fields=fields,
            events=tuple(result.events),
            tool_calls=tuple(result.tool_calls),
            error=error,
        )

    def close(self) -> None:
        return None


class RetrievalQualityAdapter:
    """跑单条 golden 查询,暴露 recall@k / MRR / 冗余度。"""

    name = "retrieval-quality"

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        config = dict(options or {})
        self.variant = str(config.get("variant", "bm25_mmr"))
        if self.variant not in RANKERS:
            raise ValueError(
                f"未知检索口径 {self.variant!r},支持 {sorted(RANKERS)}"
            )

    def run(self, case: PlatformCase) -> Observation:
        query = case.input.get("query")
        relevant = case.input.get("relevant")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{case.id}:input.query 必须是非空字符串")
        if not isinstance(relevant, list) or not relevant:
            raise ValueError(f"{case.id}:input.relevant 必须是非空列表")
        variant = str(case.input.get("variant") or self.variant)
        if variant not in RANKERS:
            raise ValueError(
                f"{case.id}:未知检索口径 {variant!r},支持 {sorted(RANKERS)}"
            )
        golden = GoldenQuery(
            query=query,
            relevant=tuple(str(item) for item in relevant),
        )
        report = run_variant(variant, queries=(golden,), docs=GOLDEN_DOCS)
        detail = dict(report["details"][0])
        ranked = [str(item) for item in detail.get("ranked", [])]
        metrics = {
            "recall_at_k": float(detail.get("recall", 0.0)),
            "mrr": float(detail.get("rr", 0.0)),
            "redundancy": float(detail.get("redundancy", 0.0)),
        }
        return Observation(
            output=" ".join(ranked),
            fields={
                "ranked": ranked,
                "metrics": metrics,
                "variant": variant,
            },
        )

    def close(self) -> None:
        return None
