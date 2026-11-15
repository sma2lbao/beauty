"""R7 抽取质量评估:用固定 golden 对话 + 脚本化模型输出量抽取链路质量。

不接真实模型(零网络):模型原始输出以固定 JSON 字符串注入,评估的是
"解析 → 复核分流 → 去重 → 落库"这段可回归的确定性逻辑,以及 prompt v2
要求的 confidence/evidence 字段是否真的被用上。指标:

- precision:进入系统(落库+待复核)的条目中,属于 golden 期望的比例;
- recall:golden 期望条目被抽到(落库或待复核)的比例;
- duplicate_rate:被去重丢弃的候选占比(越高说明 prompt 越啰嗦);
- acceptance_rate:落库条目占"进入系统"条目的比例(其余进复核队列);
- queue_rate:低置信度进入复核的比例。

运行方式(需临时 sqlite):``python -m agent_orchestrator.evals.memory_quality``
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import memory_reviews
from agent_orchestrator.services.memory_extraction import remember_candidates
from agent_orchestrator.services.memory_extraction_prompt import parse_candidates

#: 低置信度阈值(与 Settings.memory_review_confidence_threshold 默认一致)。
REVIEW_THRESHOLD = 0.6


@dataclass(frozen=True)
class GoldenCase:
    """一个 golden 对话:模型原始输出 + 期望被记住的事实。"""

    case_id: str
    raw_model_output: str
    expected: tuple[str, ...]


@dataclass
class CaseMetrics:
    case_id: str
    produced: int = 0
    written: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    matched_expected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "produced": self.produced,
            "written": list(self.written),
            "queued": list(self.queued),
            "expected": list(self.expected),
            "matched_expected": list(self.matched_expected),
        }


#: 6 条 golden 用例:正常偏好/事实、重复候选、低置信度、敏感内容、闲聊噪声。
GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        case_id="preference-table",
        raw_model_output='[{"kind": "preference", "content": "用户偏好周报用表格", '
        '"scope": "account", "confidence": 0.9, "evidence_quote": "以后周报都用表格"}]',
        expected=("用户偏好周报用表格",),
    ),
    GoldenCase(
        case_id="fact-project-code",
        raw_model_output='[{"kind": "fact", "content": "项目代号是蓝鲸", '
        '"scope": "account", "confidence": 0.95}]',
        expected=("项目代号是蓝鲸",),
    ),
    GoldenCase(
        case_id="duplicate-candidate",
        raw_model_output='[{"kind": "preference", "content": "用户偏好简洁回答", '
        '"confidence": 0.8}, {"kind": "preference", "content": "用户偏好简洁回答", '
        '"confidence": 0.8}]',
        expected=("用户偏好简洁回答",),
    ),
    GoldenCase(
        case_id="low-confidence",
        raw_model_output='[{"kind": "preference", "content": "用户可能喜欢深色主题", '
        '"confidence": 0.3, "evidence_quote": "也许吧"}]',
        expected=("用户可能喜欢深色主题",),
    ),
    GoldenCase(
        case_id="sensitive-rejected",
        raw_model_output=(
            '[{"kind": "note", "content": "API key 是 sk-abcdefghijklmnop", '
            '"confidence": 0.9}, {"kind": "fact", "content": "部署环境是首尔", '
            '"confidence": 0.9}]'
        ),
        expected=("部署环境是首尔",),
    ),
    GoldenCase(
        case_id="chatter-noise",
        raw_model_output='[{"kind": "note", "content": "今天天气不错", '
        '"confidence": 0.9}]',
        expected=(),
    ),
)


def _similar(left: str, right: str) -> bool:
    return memory_service.content_similarity(left, right) >= 0.9


async def run_case(
    session_factory: async_sessionmaker[AsyncSession],
    case: GoldenCase,
) -> CaseMetrics:
    """跑一条 golden 用例:解析原始输出 → 复核分流/去重 → 统计落库与队列。"""
    candidates = parse_candidates(case.raw_model_output, max_items=10)
    metrics = CaseMetrics(
        case_id=case.case_id,
        produced=len(candidates),
        expected=list(case.expected),
    )
    account_id = f"eval-{case.case_id}"
    async with session_factory() as session:
        # 低置信度先走复核队列;其余走正常写入(带去重)。
        high = [item for item in candidates if item.confidence >= REVIEW_THRESHOLD]
        low = [item for item in candidates if item.confidence < REVIEW_THRESHOLD]
        await remember_candidates(
            session,
            account_id,
            candidates=high,
            source_type="chat",
            source_id="eval",
        )
        for item in low:
            await memory_reviews.queue_candidate(
                session,
                account_id,
                content=item.content,
                kind=item.kind,
                scope=item.scope,
                confidence=item.confidence,
                evidence_quote=item.evidence_quote,
            )
        await session.commit()
        active = await memory_service.list_memories(session, account_id)
        pending = await memory_reviews.list_reviews(session, account_id)
    metrics.written = [row.content for row in active]
    metrics.queued = [row.content for row in pending]
    for expected in case.expected:
        if any(_similar(expected, text) for text in metrics.written + metrics.queued):
            metrics.matched_expected.append(expected)
    return metrics


def aggregate(metrics: Sequence[CaseMetrics]) -> dict[str, Any]:
    """把逐用例指标汇总成可对比的报告(供 CI/人工回归看趋势)。"""
    produced = sum(item.produced for item in metrics)
    entered = sum(len(item.written) + len(item.queued) for item in metrics)
    expected = sum(len(item.expected) for item in metrics)
    matched = sum(len(item.matched_expected) for item in metrics)
    written = sum(len(item.written) for item in metrics)
    queued = sum(len(item.queued) for item in metrics)
    # produced - entered 同时包含"重复被去重"和"敏感被拒";两者都是应过滤的候选。
    filtered_rate = 0.0 if produced == 0 else (produced - entered) / produced
    return {
        "cases": len(metrics),
        "produced": produced,
        "entered": entered,
        "written": written,
        "queued": queued,
        "expected": expected,
        "matched": matched,
        "precision": round(matched / entered, 4) if entered else 0.0,
        "recall": round(matched / expected, 4) if expected else 1.0,
        "filtered_rate": round(filtered_rate, 4),
        "acceptance_rate": round(written / entered, 4) if entered else 0.0,
        "queue_rate": round(queued / entered, 4) if entered else 0.0,
    }


async def run_golden_suite(
    session_factory: async_sessionmaker[AsyncSession],
    cases: Sequence[GoldenCase] = GOLDEN_CASES,
) -> dict[str, Any]:
    """跑全部 golden 用例并返回逐用例 + 汇总报告。"""
    results = [await run_case(session_factory, case) for case in cases]
    return {
        "metrics": aggregate(results),
        "cases": [item.to_dict() for item in results],
    }


def _settings(tmp_path: Path) -> Any:
    """独立运行时的最小设置(评测不需要真实服务)。"""
    from tests.conftest import _settings as base_settings

    return base_settings(tmp_path, "memory-quality.db")


async def _main() -> None:  # pragma: no cover - 手工运行入口
    import tempfile

    from database.base import Base
    from database.session import create_session_factory

    with tempfile.TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        factory = create_session_factory(settings.database_url)
        try:
            async with factory.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            report = await run_golden_suite(factory)
        finally:
            await factory.engine.dispose()
    import json

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(_main())
