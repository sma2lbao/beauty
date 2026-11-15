"""R7 抽取质量评估的回归测试:golden 用例在临时库上跑通,指标不低于基线。

固定候选输入(零网络)保证指标可复现;阈值是"不倒退"的下限,不是模型质量上限。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from database.base import Base
from database.session import create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.evals.memory_quality import run_golden_suite
from tests.conftest import _settings


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = _settings(tmp_path, "memory-quality-eval.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


async def test_golden_suite_meets_quality_floor(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    report = await run_golden_suite(factory)
    metrics = report["metrics"]
    assert metrics["cases"] == 6
    # 敏感内容与闲聊噪声不进入系统;重复候选在解析阶段就按内容折叠(7 条候选,6 条进入)。
    assert metrics["produced"] == 7
    assert metrics["filtered_rate"] == pytest.approx(1 / 7, abs=1e-3)
    # 5 条 golden 事实全部命中(高置信落库 + 低置信进复核)。
    assert metrics["recall"] == 1.0
    assert metrics["entered"] == 6
    assert metrics["written"] == 5
    assert metrics["queued"] == 1
    # 进入系统的条目里,只有一条是低置信待复核,其余都是真值。
    assert metrics["precision"] >= 0.8
    assert metrics["acceptance_rate"] >= 0.8
