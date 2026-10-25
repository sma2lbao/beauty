"""工具输出信封解析:ok 判定、错误码与审计字段(设计 §6.5/§11.1 兼容)。

orchestrator 需要把工具回喂结果判成 ok/失败写进审计与 SSE。旧注册表用
「以 {"error" 开头」做字符串前缀判断,换成 tool-service 的 JSON 信封后不
够用:信封成功时 {"ok": true, ...}、失败时 {"ok": false, "error": {...}}。
这里集中一处解析,图节点与对话循环共用,避免两处口径漂移:

- :func:`is_tool_error`:成功/失败判定;
- :func:`parse_tool_error`:失败结果的 error_code(审计字段);
- :func:`tool_result_fields`:成功信封里的 truncated/paths/sha256 审计字段。
"""

from __future__ import annotations

import json
from typing import Any

#: 旧注册表 _error() 的固定前缀(json.dumps 的空格稳定,直接前缀兜底)。
LEGACY_ERROR_PREFIX = '{"error"'
#: 旧注册表错误没有结构化错误码:统一落这个码,保持 error_code 可审计。
LEGACY_ERROR_CODE = "tool.error"

#: 审计字段长度上限(error_code VARCHAR(64) / sha256 CHAR(64))。
MAX_ERROR_CODE_CHARS = 64
MAX_SHA256_CHARS = 64
#: 单条记录里保留的路径条数上限(事件 payload 侧还会再截到 20 条)。
MAX_RESULT_PATHS = 50


def _parse_object(output: Any) -> dict[str, Any] | None:
    """把工具输出解析成 JSON 对象;非 JSON/非对象一律回 None。"""

    if not isinstance(output, str):
        return None
    text = output.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def is_tool_error(output: str) -> bool:
    """判断一次工具回喂结果是否失败。

    规则:

    - 能解析成 JSON 对象且带布尔 ok:以 ok 为准;
    - 能解析成 JSON 对象但没有 ok:含 error 键即失败(旧 {"error": ...});
    - 不是 JSON 对象:按旧前缀规则判断(兼容纯文本工具结果)。
    """

    if not isinstance(output, str):
        return True
    if not output.strip():
        return False
    payload = _parse_object(output)
    if payload is not None:
        if payload.get("ok") is False:
            return True
        if payload.get("ok") is True:
            return False
        return "error" in payload
    return output.startswith(LEGACY_ERROR_PREFIX)


def parse_tool_error(output: str) -> str | None:
    """解析失败结果的 error_code(设计 §11.1);成功/纯文本结果回 None。

    - 新信封 {"ok": false, "error": {"code": ...}} -> error.code;
    - 旧信封 {"error": "文本"}(含被截断的旧 JSON)-> LEGACY_ERROR_CODE;
    - 成功信封/纯文本 -> None。
    """

    if not is_tool_error(output):
        return None
    payload = _parse_object(output)
    if payload is not None:
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code[:MAX_ERROR_CODE_CHARS]
    return LEGACY_ERROR_CODE


def tool_result_fields(output: str) -> dict[str, Any]:
    """从工具结果信封提取审计字段:truncated / paths / sha256_before|after。

    tool-service 成功信封把结果字段平铺在顶层(§7.2/§7.3):write 回 path、
    edit 回 path、read 也回 path;这里同时兼容单值 path 与 paths 列表。
    非 JSON 或旧信封回空 dict(调用方据此走缺省值)。
    """

    payload = _parse_object(output)
    if payload is None:
        return {}
    fields: dict[str, Any] = {}
    truncated = payload.get("truncated")
    if isinstance(truncated, bool):
        fields["truncated"] = truncated
    paths = _result_paths(payload)
    if paths:
        fields["paths"] = paths
    for key in ("sha256_before", "sha256_after"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            fields[key] = value[:MAX_SHA256_CHARS]
    return fields


def _result_paths(payload: dict[str, Any]) -> list[str]:
    """收集信封里的路径(单值 path 在前,paths 列表去重补后)。"""

    paths: list[str] = []
    single = payload.get("path")
    if isinstance(single, str) and single:
        paths.append(single)
    listed = payload.get("paths")
    if isinstance(listed, list):
        for item in listed:
            text = str(item)
            if text and text not in paths:
                paths.append(text)
    return paths[:MAX_RESULT_PATHS]
