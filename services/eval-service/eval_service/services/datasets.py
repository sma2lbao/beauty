"""数据集只读目录:直接扫描仓库 `evals/datasets/*`。

数据集是代码的一部分（随 git 评审、随镜像发布），因此不落数据库、不做上传；
服务只负责读取与校验。路径解析顺序：显式配置 -> 从 cwd 向上找
`evals/datasets` -> 模块相对路径（容器工作目录不在仓库根时的兜底）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from common.errors import AppError, NotFoundError
from evalkit.errors import SchemaError
from evalkit.schema import Dataset, load_dataset, load_manifest

from eval_service.core.config import Settings
from eval_service.schemas.evals import (
    EvalDatasetCaseRead,
    EvalDatasetRead,
    EvalDatasetSummaryRead,
)


class DatasetInvalidError(AppError):
    """数据集存在但 schema 校验失败。"""

    status = 400


class DatasetNotFoundError(NotFoundError):
    """数据集目录下没有这个名字。"""


@dataclass(frozen=True)
class _Entry:
    summary: EvalDatasetSummaryRead
    dataset: Dataset | None = None
    error: str = ""


def resolve_datasets_root(settings: Settings) -> Path:
    """定位数据集根目录;显式配置优先，其次仓库布局，最后模块相对路径。"""

    configured = settings.datasets_root.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidate = base / "evals" / "datasets"
        if candidate.is_dir():
            return candidate
    # services/eval-service/eval_service/services/datasets.py -> 仓库根
    return (Path(__file__).resolve().parents[4] / "evals" / "datasets").resolve()


def catalog_datasets(root: Path) -> list[EvalDatasetSummaryRead]:
    """列出全部数据集摘要;坏数据集保留在清单里并带 error，不静默消失。"""

    return [entry.summary for entry in _entries(root)]


def load_catalog_dataset(root: Path, name: str) -> EvalDatasetRead:
    """读取单个数据集详情;不存在 404，schema 不合法 400。"""

    for entry in _entries(root):
        if entry.summary.name != name:
            continue
        if entry.dataset is None:
            raise DatasetInvalidError(
                "eval.dataset_invalid",
                f"数据集 {name} 校验失败:{entry.error}",
                details={"dataset": name, "error": entry.error},
            )
        return _detail(entry.dataset)
    raise DatasetNotFoundError(
        "eval.dataset_not_found",
        f"数据集 {name} 不存在",
        details={"dataset": name},
    )


def _entries(root: Path) -> list[_Entry]:
    if not root.is_dir():
        return []
    entries = [
        _read_entry(directory)
        for directory in sorted(root.iterdir(), key=lambda item: item.name)
        if directory.is_dir()
        and (
            (directory / "manifest.json").is_file()
            or (directory / "cases.jsonl").is_file()
        )
    ]
    return sorted(entries, key=lambda entry: entry.summary.name)


def _read_entry(directory: Path) -> _Entry:
    manifest_path = directory / "manifest.json"
    fallback = EvalDatasetSummaryRead(
        name=directory.name,
        valid=False,
        error="缺少 manifest.json",
    )
    if not manifest_path.is_file():
        return _Entry(summary=fallback, error=fallback.error or "")

    try:
        manifest = load_manifest(manifest_path)
    except SchemaError as exc:
        error = str(exc)
        return _Entry(
            summary=fallback.model_copy(update={"error": error}),
            error=error,
        )

    try:
        dataset = load_dataset(directory)
    except SchemaError as exc:
        error = str(exc)
        return _Entry(
            summary=_summary(manifest, valid=False, error=error),
            error=error,
        )
    return _Entry(
        summary=_summary(manifest, valid=True, dataset=dataset),
        dataset=dataset,
    )


def _summary(
    manifest,
    *,
    valid: bool,
    error: str = "",
    dataset: Dataset | None = None,
) -> EvalDatasetSummaryRead:
    cases = dataset.cases if dataset is not None else ()
    return EvalDatasetSummaryRead(
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        owner=manifest.owner,
        adapter=manifest.adapter,
        tags=list(manifest.tags),
        case_count=len(cases),
        groups=sorted({case.group for case in cases}),
        valid=valid,
        error=error or None,
    )


def _detail(dataset: Dataset) -> EvalDatasetRead:
    cases = [
        EvalDatasetCaseRead(
            id=case.id,
            group=case.group,
            tags=list(case.tags),
            input=dict(case.input),
            graders=[dict(grader) for grader in case.graders],
            notes=case.notes,
        )
        for case in dataset.cases
    ]
    manifest = dataset.manifest
    groups = sorted({case.group for case in dataset.cases})
    return EvalDatasetRead(
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        owner=manifest.owner,
        adapter=manifest.adapter,
        tags=list(manifest.tags),
        case_count=len(cases),
        groups=groups,
        cases=cases,
    )
