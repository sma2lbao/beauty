"""数据集目录:路径解析、schema 校验错误不静默、详情转换。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from common.errors import AppError

from eval_service.services.datasets import (
    catalog_datasets,
    load_catalog_dataset,
    resolve_datasets_root,
)
from tests.conftest import _settings


def write_dataset(
    root: Path,
    name: str,
    cases: list[dict],
    *,
    version: int = 1,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "description": "测试数据集",
                "owner": "tests",
                "adapter": "tests.adapter:Adapter",
                "tags": ["offline"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (directory / "cases.jsonl").write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases),
        encoding="utf-8",
    )
    return directory


def grader() -> dict:
    return {"type": "contains", "value": "你好"}


def test_resolve_root_prefers_explicit_setting(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()
    settings = _settings(tmp_path, datasets_root=str(root))
    assert resolve_datasets_root(settings) == root.resolve()


def test_catalog_and_detail_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()
    write_dataset(
        root,
        "agent-core",
        [
            {
                "id": "chat/a",
                "group": "chat",
                "input": {"message": "hi"},
                "graders": [grader()],
            },
            {
                "id": "chat/b",
                "group": "chat",
                "input": {"message": "yo"},
                "graders": [grader()],
            },
            {
                "id": "task/c",
                "group": "task",
                "input": {"goal": "x"},
                "graders": [grader()],
            },
        ],
    )
    (root / "notes").mkdir()  # 非数据集目录应被忽略

    items = catalog_datasets(root)
    assert [item.name for item in items] == ["agent-core"]
    assert items[0].valid is True
    assert items[0].case_count == 3
    assert items[0].groups == ["chat", "task"]
    assert items[0].tags == ["offline"]

    detail = load_catalog_dataset(root, "agent-core")
    assert detail.case_count == 3
    assert detail.cases[0].id == "chat/a"
    assert detail.cases[0].input == {"message": "hi"}
    assert detail.cases[0].graders[0]["type"] == "contains"


def test_invalid_dataset_stays_in_catalog_and_detail_is_400(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()
    directory = write_dataset(root, "broken", [{"id": "chat/a"}])
    (directory / "cases.jsonl").unlink()

    items = catalog_datasets(root)
    assert items[0].name == "broken"
    assert items[0].valid is False
    assert "cases.jsonl" in (items[0].error or "")

    with pytest.raises(AppError) as caught:
        load_catalog_dataset(root, "broken")
    assert caught.value.code == "eval.dataset_invalid"
    assert caught.value.status == 400


def test_missing_manifest_and_unknown_dataset(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()
    empty = root / "empty"
    empty.mkdir()
    (empty / "cases.jsonl").write_text("", encoding="utf-8")

    items = catalog_datasets(root)
    assert items[0].name == "empty"
    assert items[0].valid is False
    assert items[0].error == "缺少 manifest.json"

    with pytest.raises(AppError) as caught:
        load_catalog_dataset(root, "nope")
    assert caught.value.code == "eval.dataset_not_found"
    assert caught.value.status == 404


def test_bad_manifest_json_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    directory = root / "bad-json"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{not json", encoding="utf-8")

    items = catalog_datasets(root)
    assert items[0].valid is False
    assert "manifest" in (items[0].error or "")


def test_missing_root_catalogs_empty(tmp_path: Path) -> None:
    assert catalog_datasets(tmp_path / "not-there") == []
