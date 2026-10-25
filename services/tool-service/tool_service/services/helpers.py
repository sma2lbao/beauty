"""沙箱 helper 的源码打包与请求组装。

helper 是纯 stdlib 脚本(helpers/fs_helper.py),不进镜像的 import 图:
执行时把源码作为 python3 -c 的 argv 传进去,模型数据(文件内容、pattern)
只走 stdin,因此不会出现在进程命令行里。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tool_service.constants import build_policy

#: 服务根目录下的 helper 源码路径(tool_service/services/helpers.py -> 上两级)。
HELPER_PATH = Path(__file__).resolve().parents[2] / "helpers" / "fs_helper.py"

#: 沙箱内的工作区挂载点(helper 的路径 jail 根)。
WORKSPACE_ROOT = "/workspace"
#: 沙箱内解释器:镜像里必须存在 python3。
HELPER_PYTHON = "python3"


@lru_cache(maxsize=1)
def helper_source() -> str:
    """读取 helper 源码(进程生命周期内只读一次盘)。"""

    return HELPER_PATH.read_text(encoding="utf-8")


def helper_argv() -> list[str]:
    """构造沙箱 exec 的 argv;不带任何模型数据。"""

    return [HELPER_PYTHON, "-c", helper_source()]


def helper_env(workspace_root: str = WORKSPACE_ROOT) -> dict[str, str]:
    """helper 需要的环境变量(路径 jail 根)。"""

    return {"BM_WORKSPACE_ROOT": workspace_root}


def build_request(tool: str, arguments: dict[str, Any]) -> str:
    """组装 stdin 请求体:工具名 + 参数 + 路径策略。"""

    payload = {
        "tool": tool,
        "arguments": arguments,
        "policy": build_policy(),
    }
    return json.dumps(payload, ensure_ascii=False)
