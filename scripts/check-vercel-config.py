#!/usr/bin/env python3
"""check-vercel-config.py —— 校验无状态服务的 Vercel 导出配置一致性。

Vercel Python runtime 只自动识别根级 ``app.py`` / ``main.py`` 等入口；本仓库 6 个
无状态服务的 ``app`` 在 ``<pkg>/main.py``，因此导出依赖四处配置共同成立：

1. ``pyproject.toml`` 的 ``[tool.vercel] entrypoint = "<pkg>.main:app"``；
2. ``vercel.json`` 的 ``functions`` 以入口文件 ``<pkg>/main.py`` 为 key（含
   ``maxDuration`` 与 ``excludeFiles``，控制函数时长与 500MB 包体）；``maxDuration``
   还必须不超过部署计划上限（``MAX_FUNCTION_DURATION``，Hobby 300s；800s 需 Pro），
   否则部署阶段会被 Vercel 直接拒绝（详见 <docs/design/serverless-deployment.md> §7.6）；
3. ``vercel.json`` 显式覆盖 ``framework`` / ``installCommand`` / ``buildCommand``：
   仓库根有 ``nx.json``，Vercel 会命中 Nx monorepo 自动配置，把安装/构建命令改成
   ``npm install --prefix=../..`` + ``npx nx build <项目>``；而本仓库 Python 服务
   刻意没有 build target，不覆盖就必然以 ``Cannot find configuration for task`` 失败。
   ``framework = "fastapi"`` 钉住框架预设；``installCommand`` 显式跑 uv workspace
   安装（``uv sync --active --no-dev --no-editable --package <服务> --frozen``）——
   **不能留空字符串**：留空等于跳过 Install Command，实测函数 bundle 里没有任何
   第三方依赖，运行时报 ``ModuleNotFoundError: No module named 'httpx'``（详见
   <docs/design/serverless-deployment.md> §7.6）；``buildCommand`` 用 no-op
   （``true``）代替被劫持的 nx 构建；
4. ``.python-version`` 固定 3.12（与根 ``.python-version`` 一致，避免 Vercel 按
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
    "api-gateway": "api_gateway",
    "account-service": "account",
    "llm-gateway": "llm_gateway",
    "connector-service": "connector",
    "knowledge-service": "knowledge",
    "tool-service": "tool_service",
}

PYTHON_VERSION = "3.12"
# Vercel 检测到仓库根 nx.json 后会注入 Nx 的 install/build 命令，Python 服务必须显式覆盖。
EXPECTED_FRAMEWORK = "fastapi"
# 函数时长的部署计划硬上限：Vercel 在部署时校验，超限直接拒绝部署
# （``The value for maxDuration must be between 1 second and 300 seconds``）。
# Hobby 默认与最大都是 300s；800s 只有 Pro/Enterprise 可用。
# 部署目标升级到 Pro 后，与 services/api-gateway/vercel.json 的 maxDuration 一起调高。
MAX_FUNCTION_DURATION = 300
# 依赖安装命令：builder 会先按 .python-version 建好 venv，再带 VIRTUAL_ENV 执行本命令，
# 所以必须用 --active 装进那个 venv（否则装到另一份 .venv，bundle 里依旧是空的）。
# --no-dev    不装 [dependency-groups].dev 里的测试依赖；
# --no-editable 把 common 等 workspace 成员装成普通包，随 _vendor 一起进 bundle
#             （editable 的 .pth 指向 ../../packages/*，运行时不存在）；
# --package   只装该服务依赖，否则整 workspace 11 个项目全装；
# --frozen    用仓库根已提交的 uv.lock，构建期不改锁文件。
INSTALL_COMMAND_TEMPLATE = (
    "uv sync --active --no-dev --no-editable --package {service} --frozen"
)
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
            if config.get("framework") != EXPECTED_FRAMEWORK:
                problems.append(
                    f"{vercel_path.relative_to(ROOT)}: framework 应为 "
                    f"{EXPECTED_FRAMEWORK!r}，实际 {config.get('framework')!r}"
                )
            expected_install = INSTALL_COMMAND_TEMPLATE.format(service=service)
            install_command = config.get("installCommand")
            if install_command != expected_install:
                problems.append(
                    f"{vercel_path.relative_to(ROOT)}: installCommand 应为 "
                    f"{expected_install!r}（覆盖 Nx 注入的 npm install，并按 uv "
                    f"workspace 把依赖装进 builder 的 venv），实际 "
                    f"{install_command!r}"
                )
            build_command = config.get("buildCommand")
            if not isinstance(build_command, str) or not build_command.strip():
                problems.append(
                    f"{vercel_path.relative_to(ROOT)}: buildCommand 必须是非空字符串"
                    f"（覆盖 Nx 注入的 nx build），实际 {build_command!r}"
                )
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
                elif duration > MAX_FUNCTION_DURATION:
                    problems.append(
                        f"{vercel_path.relative_to(ROOT)}: {key} 的 maxDuration "
                        f"{duration}s 超过计划上限 {MAX_FUNCTION_DURATION}s"
                        f"（Vercel: between 1 and {MAX_FUNCTION_DURATION} seconds，"
                        f"升级 Pro 才可到 800s）"
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
