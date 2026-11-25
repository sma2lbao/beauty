"""数据集只读目录:清单与单数据集详情。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from eval_service.api.deps import get_settings, get_viewer_account
from eval_service.core.config import Settings
from eval_service.schemas.evals import EvalDatasetListRead, EvalDatasetRead
from eval_service.services import (
    catalog_datasets,
    load_catalog_dataset,
    resolve_datasets_root,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])

ViewerDep = Annotated[str | None, Depends(get_viewer_account)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=EvalDatasetListRead)
async def list_datasets_endpoint(
    _: ViewerDep, settings: SettingsDep
) -> EvalDatasetListRead:
    root = resolve_datasets_root(settings)
    return EvalDatasetListRead(items=catalog_datasets(root))


@router.get("/{name}", response_model=EvalDatasetRead)
async def get_dataset_endpoint(
    name: str, _: ViewerDep, settings: SettingsDep
) -> EvalDatasetRead:
    return load_catalog_dataset(resolve_datasets_root(settings), name)
