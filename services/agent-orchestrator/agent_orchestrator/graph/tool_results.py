"""工具输出 ok 判定:JSON 信封优先,旧纯文本回退(设计 §6.5 兼容)。

orchestrator 需要把工具回喂结果判成 ok/失败写进审计与 SSE。旧注册表用
「以 {"error" 开头」做字符串前缀判断,换成 tool-service 的 JSON 信封后不
够用:信封成功时 {"ok": true, ...}、失败时 {"ok": false, "error": {...}}。
这里集中一处判定,图节点与对话循环共用,避免两处口径漂移。
"""

from __future__ import annotations

import json

#: 旧注册表 _error() 的固定前缀(json.dumps 的空格稳定,直接前缀兜底)。
LEGACY_ERROR_PREFIX = '{"error"'


def is_tool_error(output: str) -> bool:
    """判断一次工具回喂结果是否失败。

    规则:

    - 能解析成 JSON 对象且带布尔 ok:以 ok 为准;
    - 能解析成 JSON 对象但没有 ok:含 error 键即失败(旧 {"error": ...});
    - 不是 JSON 对象:按旧前缀规则判断(兼容纯文本工具结果)。
    """

    if not isinstance(output, str):
        return True
    text = output.strip()
    if not text:
        return False
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if payload.get("ok") is False:
                return True
            if payload.get("ok") is True:
                return False
            return "error" in payload
    return output.startswith(LEGACY_ERROR_PREFIX)
