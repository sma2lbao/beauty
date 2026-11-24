"""把 agent-orchestrator、检索与记忆抽取接入统一评测平台。

adapter 放在服务包内而不是 evalkit:平台核心不反向依赖具体服务,服务自己
负责把内部链路包装成 Observation。三条 adapter 都是进程内、零网络。
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from database.base import Base
from database.session import create_session_factory
from evalkit.graders import Observation
from evalkit.schema import Case as PlatformCase

from .memory_quality import CaseMetrics, GoldenCase, aggregate
from .memory_quality import run_case as run_memory_case
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
            "messages": case.input.get("messages", []),
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
            "tool_call_names": list(result.tool_calls),
            "tool_call_details": [dict(detail) for detail in result.tool_call_details],
            "tool_call_arguments": [
                detail.get("arguments") for detail in result.tool_call_details
            ],
            "tool_call_count": len(result.tool_call_details),
            "replies": list(result.replies),
            "turn_count": len(result.replies),
            "run_detail": dict(result.run_detail),
            "parent_run_detail": dict(result.parent_run_detail),
            "parent_run_id": result.run_detail.get("parent_run_id"),
            "parent_status": result.parent_run_detail.get("status"),
            "parent_kind": result.run_detail.get("parent_kind"),
            "run_title": result.run_detail.get("title"),
        }
        # 多轮用例按轮暴露回复与模型入参:grader 可直接验证"上一轮确实注入"。
        for index, reply in enumerate(result.replies):
            fields[f"reply_{index}"] = reply
        for index, prompt in enumerate(result.prompts):
            fields[f"prompt_{index}"] = prompt
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


class MemoryQualityAdapter:
    """跑单条 golden 记忆用例,暴露抽取/过滤/复核指标。

    每条用例独立临时 sqlite 与独立事件循环:async engine 不能跨循环复用,
    而 evalkit 的 adapter 协议是同步的。输出只含计数,不回显记忆正文。
    """

    name = "memory-quality"

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})

    def run(self, case: PlatformCase) -> Observation:
        raw = case.input.get("raw_model_output")
        expected = case.input.get("expected", [])
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{case.id}:input.raw_model_output 必须是非空字符串")
        if not isinstance(expected, list) or not all(
            isinstance(item, str) and item for item in expected
        ):
            raise ValueError(f"{case.id}:input.expected 必须是字符串列表")
        golden = GoldenCase(
            case_id=case.id,
            raw_model_output=raw,
            expected=tuple(expected),
        )
        metrics = asyncio.run(self._run_golden(golden))
        totals = aggregate([metrics])
        fields = {
            "metrics": totals,
            "produced": metrics.produced,
            "entered": len(metrics.written) + len(metrics.queued),
            "written": list(metrics.written),
            "queued": list(metrics.queued),
            "expected_items": list(metrics.expected),
            "matched_items": list(metrics.matched_expected),
            "written_count": len(metrics.written),
            "queued_count": len(metrics.queued),
        }
        output = (
            f"produced={fields['produced']} entered={fields['entered']} "
            f"written={fields['written_count']} queued={fields['queued_count']}"
        )
        return Observation(output=output, fields=fields)

    async def _run_golden(self, case: GoldenCase) -> CaseMetrics:
        with tempfile.TemporaryDirectory(prefix="eval-memory-") as tmp:
            url = f"sqlite+aiosqlite:///{Path(tmp) / 'eval.db'}"
            factory = create_session_factory(url)
            try:
                async with factory.engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                return await run_memory_case(factory, case)
            finally:
                await factory.engine.dispose()

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
