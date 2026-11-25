"""评测服务仓储:全局数据(不按账号隔离),仓储不提交。"""

from __future__ import annotations

import json
from typing import Any

from database.repository import Repository
from sqlalchemy import select
from sqlalchemy.sql.elements import ColumnElement

from eval_service.models import EvalBaseline, EvalCaseRun, EvalRun, EvalSuiteRun


class EvalRunRepository(Repository[EvalRun]):
    """运行主表:外部键幂等 + 运维过滤 + 倒序分页。"""

    model = EvalRun
    not_found_code = "eval.run_not_found"
    not_found_message = "评测运行不存在"
    id_detail_key = "run_id"

    async def find_external(self, external_id: str) -> EvalRun | None:
        return await self.session.scalar(
            select(EvalRun).where(EvalRun.external_id == external_id)
        )

    async def list_page(
        self,
        *,
        suite: str | None = None,
        status: str | None = None,
        branch: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EvalRun]:
        criteria = self._filters(suite=suite, status=status, branch=branch)
        statement = (
            select(EvalRun)
            .where(*criteria)
            .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(await self.session.scalars(statement))

    async def count_page(
        self,
        *,
        suite: str | None = None,
        status: str | None = None,
        branch: str | None = None,
    ) -> int:
        return await self.count(
            *self._filters(suite=suite, status=status, branch=branch)
        )

    def _filters(
        self,
        *,
        suite: str | None,
        status: str | None,
        branch: str | None,
    ) -> tuple[ColumnElement[bool], ...]:
        criteria: list[ColumnElement[bool]] = []
        if suite:
            criteria.append(
                EvalRun.id.in_(
                    select(EvalSuiteRun.run_id).where(EvalSuiteRun.name == suite)
                )
            )
        if branch:
            criteria.append(EvalRun.branch == branch)
        if status == "passed":
            criteria.append(EvalRun.failed_count == 0)
        elif status == "failed":
            criteria.append(EvalRun.failed_count > 0)
        elif status == "drift":
            criteria.append(EvalRun.drift_count > 0)
        return tuple(criteria)


class EvalSuiteRunRepository(Repository[EvalSuiteRun]):
    """套件摘要:按运行读取,套件名唯一。"""

    model = EvalSuiteRun

    async def list_for_run(self, run_id: str) -> list[EvalSuiteRun]:
        return list(
            await self.session.scalars(
                select(EvalSuiteRun)
                .where(EvalSuiteRun.run_id == run_id)
                .order_by(EvalSuiteRun.name)
            )
        )

    async def get_for_run(self, run_id: str, name: str) -> EvalSuiteRun | None:
        return await self.session.scalar(
            select(EvalSuiteRun).where(
                EvalSuiteRun.run_id == run_id, EvalSuiteRun.name == name
            )
        )


class EvalCaseRunRepository(Repository[EvalCaseRun]):
    """用例结果:服务端分页与状态过滤。"""

    model = EvalCaseRun

    async def list_page(
        self,
        run_id: str,
        suite: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EvalCaseRun]:
        statement = (
            select(EvalCaseRun)
            .where(*self._filters(run_id, suite, status))
            .order_by(EvalCaseRun.case_id)
            .limit(limit)
            .offset(offset)
        )
        return list(await self.session.scalars(statement))

    async def count_page(
        self,
        run_id: str,
        suite: str,
        *,
        status: str | None = None,
    ) -> int:
        return await self.count(*self._filters(run_id, suite, status))

    def _filters(
        self, run_id: str, suite: str, status: str | None
    ) -> tuple[ColumnElement[bool], ...]:
        criteria: list[ColumnElement[bool]] = [
            EvalCaseRun.run_id == run_id,
            EvalCaseRun.suite == suite,
        ]
        if status == "passed":
            criteria.append(EvalCaseRun.ok.is_(True))
        elif status == "failed":
            criteria.append(EvalCaseRun.ok.is_(False))
        return tuple(criteria)


class EvalBaselineRepository(Repository[EvalBaseline]):
    """已批准基线:每个套件一条,重复批准即替换。"""

    model = EvalBaseline

    async def get_by_suite(self, suite: str) -> EvalBaseline | None:
        return await self.session.scalar(
            select(EvalBaseline).where(EvalBaseline.suite == suite)
        )

    async def list_all(self) -> list[EvalBaseline]:
        return list(
            await self.session.scalars(
                select(EvalBaseline).order_by(EvalBaseline.suite)
            )
        )

    async def upsert(
        self,
        *,
        suite: str,
        payload_json: str,
        dataset_version: int | None,
        evaluator_version: str,
        git_sha: str,
        approved_by: str,
    ) -> EvalBaseline:
        record = await self.get_by_suite(suite)
        if record is None:
            record = EvalBaseline(suite=suite, payload_json=payload_json)
            self.session.add(record)
        record.payload_json = payload_json
        record.dataset_version = dataset_version
        record.evaluator_version = evaluator_version
        record.git_sha = git_sha
        record.approved_by = approved_by
        await self.session.flush()
        return record


def json_list(value: Any) -> list[Any]:
    """读 JSON 数组列;坏数据降级成空列表(展示层不应 500)。"""

    if not isinstance(value, str) or not value.startswith("["):
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def json_object(value: Any) -> dict[str, Any]:
    """读 JSON 对象列;坏数据降级成空对象。"""

    if not isinstance(value, str) or not value.startswith("{"):
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
