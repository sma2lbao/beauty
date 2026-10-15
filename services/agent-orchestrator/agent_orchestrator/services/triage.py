"""意图分流(§6.7):简单对话直接答,复杂任务才进 run 流程。

判别顺序:

1. 显式指定(mode=chat|task)优先——用户永远能覆盖自动判断;
2. 有模型时让模型判别,只认 JSON {"mode": "chat"|"task"};
   判别调用失败或解析不出 → 按任务处理(任务路径有 failed 兜底,
   比"悄悄当成闲聊答一句"更诚实);
3. 没有模型(LLM_ENABLED=false)时没有"直接对话"能力,一律按任务处理
   ——确定性桩仍能产出计划,语义与分流上线前一致。

显式 chat 但没有模型 → 409(chat.llm_disabled):如实告诉调用方
"无法直接对话",而不是悄悄改成任务。
"""

from __future__ import annotations

import json
from typing import Any

from common.errors import ConflictError
from common.logging import get_logger

from agent_orchestrator.services.llm import LLMClient

CHAT = "chat"
TASK = "task"
AUTO = "auto"

_log = get_logger("agent_orchestrator.triage")

TRIAGE_SYSTEM_PROMPT = (
    "你是对话分流器。判断用户这条消息该走哪条路径:\n"
    "- chat:简单对话——打招呼、闲聊、感谢、能一句话直接回答的问题、"
    "对已有结果的追问或澄清;\n"
    "- task:复杂任务——需要多步执行、调用工具或访问外部系统、产出交付物"
    "(如整理邮件成周报、生成文档、发信、安排日程)。\n"
    '只输出 JSON:{"mode": "chat"} 或 {"mode": "task"},不要输出任何其他内容。'
)


def _parse_mode(text: str) -> str | None:
    """从模型回复里取 mode;不是合法 JSON 或取值非法 → None(交给兜底)。"""
    try:
        data: Any = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("mode") in (CHAT, TASK):
        return str(data["mode"])
    return None


def require_available(mode: str, llm: LLMClient | None) -> None:
    """显式 chat 但没有模型 → 409。

    纯函数,不碰数据库也不调模型:流式端点据此在**开流之前**拒绝,
    避免出现"200 已经发了、只能塞一帧错误"的尴尬。
    """
    if mode == CHAT and llm is None:
        raise ConflictError(
            "chat.llm_disabled",
            "未启用模型,无法直接对话:请开启 LLM_ENABLED 或改用任务模式",
            details={"mode": mode},
        )


async def decide(content: str, llm: LLMClient | None, *, mode: str = AUTO) -> str:
    """决定这条消息走 chat 还是 task(判别顺序见模块 docstring)。"""
    if mode == CHAT:
        require_available(mode, llm)
        return CHAT
    if mode == TASK:
        return TASK
    if llm is None:
        return TASK
    try:
        reply = await llm.complete(
            [
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ]
        )
    except Exception:
        # 判别失败不该阻断消息:退回任务路径(失败也有 run.failed.v1 留痕)
        _log.warning("triage_failed", mode=TASK)
        return TASK
    return _parse_mode(reply.content) or TASK
