"""工具调用的稳定幂等键(S1)。

崩溃恢复/resume 会重跑 execute 节点,但模型每次生成的 `tool_call_id` 都可能
不同;tool-service 只能按 tool_call_id 去重,于是同一次写操作会被重复执行
(把文件再写一遍、把消息再发一次)。这里把"哪个 run、哪个节点、哪个工具、
什么参数"压成一个稳定的键,跨 attempt 保持不变,服务端据此重放而不是重跑。

只在 WRITE/DESTRUCTIVE 上启用:READ 的同参调用本来就该读到新数据,按键缓存
会让第二次读拿到旧结果——宁可少去重,不要返回陈旧内容。
"""

from __future__ import annotations

import hashlib
import json

#: 会改变外部世界的风险等级(与 common.tools.ToolRisk 的取值一致)。
MUTATING_RISKS = frozenset({"write", "destructive"})
#: 键前缀:算法/口径变化时换版本,避免新旧键互相碰撞。
KEY_PREFIX = "orch1"


def tool_idempotency_key(
    *,
    run_id: str | None,
    node: str,
    tool_name: str,
    arguments: str,
    risk: str,
) -> str | None:
    """算一次工具调用的稳定幂等键;不适用时返回 None。

    - 只读工具、无 run(对话路径)、参数不是 JSON 对象 → None(服务端回落
      到 tool_call_id 去重,行为与旧版一致);
    - 参数按排序后的键序列化,同一个调用的不同键序得到同一个键;
    - 不含 tool_call_id:那正是重新生成会变的东西。
    """

    if not run_id or str(risk) not in MUTATING_RISKS:
        return None
    try:
        payload = json.loads(arguments) if arguments else {}
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    material = f"{run_id}\n{node}\n{tool_name}\n{canonical}".encode()
    return f"{KEY_PREFIX}:{hashlib.sha256(material).hexdigest()}"
