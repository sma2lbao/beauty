#!/usr/bin/env python3
"""check-vercel-config.py —— 校验无状态服务的 Vercel 导出配置一致性。

Vercel Python runtime 只自动识别根级 ``app.py`` / ``main.py`` 等入口；本仓库 6 个
无状态服务的 ``app`` 在 ``<pkg>/main.py``，因此导出依赖三处配置共同成立：

1. ``pyproject.toml`` 的 ``[tool.vercel] entrypoint = "<pkg>.main:app"``；
2. ``vercel.json`` 的 ``functions`` 以入口文件 ``<pkg>/main.py`` 为 key（含
   ``maxDuration`` 与 ``excludeFiles``，控制函数时长与 500MB 包体）；
3. ``.python-version`` 固定 3.12（与根 ``.python-version`` 一致，避免 Vercel 按
   ``requires-python`` 解析到 3.13/3.14）。

任一处漂移都会让部署静默失效（Vercel 找不到入口 → 404/构建失败），所以放在 CI
的 ``check-workspace.sh`` 里卡住，而不是只靠文档约定。

用法: ``python3 scripts/check-vercel-config.py``（无参数；失败 EXIT 1 并逐条列出）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ruff 默认 target-version 把 tomllib 当成第三方库（标准库是 3.11+），故单独成组。
import tomllib

ROOT = Path(__file__).resolve().parent.parent

# 服务目录名 → Python 包名（入口文件的父目录）。
SERVICES: dict[str, str] = {
    "bff": "bff",
    "account-service": "account",
    "llm-gateway": "llm_gateway",
    "connector-service": "connector",
    "knowledge-service": "knowledge",
    "tool-service": "tool_service",
}

PYTHON_VERSION = "3.12"
APP_LINE = re.compile(r"^app\s*=\s*create_app\(\)\s*$", re.MULTILINE)


def check_service(service: str, package: str) -> list[str]:
    """返回该服务的违规项（空列表表示通过）。"""
    base = ROOT / "services" / service
    problems: list[str] = []
    expected_entry = f"{package}.main:app"

    pyproject_path = base / "pyproject.toml"
    if not pyproject_path.is_file():
        problems.append(f"{pyproject_path.relative_to(ROOT)}: 文件不存在")
    else:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        entrypoint = data.get("tool", {}).get("vercel", {}).get("entrypoint")
        if entrypoint != expected_entry:
            problems.append(
                f"{pyproject_path.relative_to(ROOT)}: [tool.vercel] entrypoint 应为 "
                f"{expected_entry!r}，实际 {entrypoint!r}"
            )

    main_path = base / package / "main.py"
    if not main_path.is_file():
        problems.append(f"{main_path.relative_to(ROOT)}: 入口文件不存在")
    elif not APP_LINE.search(main_path.read_text(encoding="utf-8")):
        problems.append(
            f"{main_path.relative_to(ROOT)}: 未定义模块级 `app = create_app()`"
        )

    vercel_path = base / "vercel.json"
    if not vercel_path.is_file():
        problems.append(f"{vercel_path.relative_to(ROOT)}: 文件不存在")
    else:
        try:
            config = json.loads(vercel_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{vercel_path.relative_to(ROOT)}: JSON 非法（{exc}）")
        else:
            functions = config.get("functions") or {}
            key = f"{package}/main.py"
            entry = functions.get(key)
            if entry is None:
                problems.append(
                    f"{vercel_path.relative_to(ROOT)}: functions 缺少入口 key "
                    f"{key!r}，实有 {sorted(functions)}"
                )
            else:
                duration = entry.get("maxDuration")
                if not isinstance(duration, int) or duration <= 0:
                    problems.append(
                        f"{vercel_path.relative_to(ROOT)}: {key} 的 maxDuration 非法"
                        f"（{duration!r}）"
                    )
                if not entry.get("excludeFiles"):
                    problems.append(
                        f"{vercel_path.relative_to(ROOT)}: {key} 缺少 excludeFiles"
                    )

    version_path = base / ".python-version"
    if not version_path.is_file():
        problems.append(f"{version_path.relative_to(ROOT)}: 文件不存在")
    else:
        version = version_path.read_text(encoding="utf-8").strip()
        if version != PYTHON_VERSION:
            problems.append(
                f"{version_path.relative_to(ROOT)}: 应为 {PYTHON_VERSION}，"
                f"实际 {version!r}"
            )

    return problems


def main() -> int:
    problems: list[str] = []
    for service, package in SERVICES.items():
        problems.extend(check_service(service, package))
    if problems:
        print("== Vercel 导出配置检查 ==")
        for problem in problems:
            print(f"  [违规] {problem}")
        print(f"== 汇总: {len(problems)} 项违规 ==")
        return 1
    print(f"== Vercel 导出配置检查: {len(SERVICES)} 个服务全部一致 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
