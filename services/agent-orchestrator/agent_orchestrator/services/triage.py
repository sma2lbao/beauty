"""意图分流(§6.7):简单对话直接答,复杂任务才进 run 流程。

判别顺序:

1. 显式指定(mode=chat|task)优先——用户永远能覆盖自动判断;
2. **带图片的消息不走任务**:任务(run)路径本期不注入图片(model-routing §7),
   判成任务等于把图丢掉,所以 auto 直接走对话直答;显式 task 带图 → 409
   (task.image_unsupported),不让用户以为图被看到了;
   带图但没模型 → 409(chat.llm_disabled):两条路径都看不见图,只能如实拒绝;
3. 有模型时让模型判别,只认 JSON {"mode": "chat"|"task"};
   判别调用失败或解析不出 → 按任务处理(任务路径有 failed 兜底,
   比"悄悄当成闲聊答一句"更诚实);
4. 没有模型(LLM_ENABLED=false)时没有"直接对话"能力,一律按任务处理
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
    "(如整理资料成周报、生成文档、检索知识库并汇总)。\n"
    '只输出 JSON:{"mode": "chat"} 或 {"mode": "task"},不要输出任何其他内容。'
)


#: 判别调用的结构化约束(P1-G §6.4):网关按 json_schema 约束输出,
#: 但解析仍保留容错——模型/网关不认这个字段时照旧按文本解析。
TRIAGE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "triage",
        "schema": {
            "type": "object",
            "properties": {"mode": {"enum": [CHAT, TASK]}},
            "required": ["mode"],
            "additionalProperties": False,
        },
    },
}


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


def require_task_images(mode: str, has_images: bool) -> None:
    """显式任务模式带图片 → 409。

    任务(run)路径本期不注入图片(model-routing §7 边界),带图任务等于把图丢掉;
    与其让用户等一跑看不见图的计划,不如开流/落库之前就说清楚。纯函数,
    流式端点据此在**开流之前**拒绝(理由同 require_available)。
    """
    if mode == TASK and has_images:
        raise ConflictError(
            "task.image_unsupported",
            "任务模式暂不支持图片输入:请改用对话模式发送图片,或去掉图片后重试",
            details={"mode": mode},
        )


def require_image_model(mode: str, llm: LLMClient | None, has_images: bool) -> None:
    """带图片但没有模型 → 409。

    任务路径不注入图片、对话路径又必须调模型:没有模型时两条路都看不见图,
    与其把它丢进任务装作处理过,不如提前说清楚。纯函数,流式端点靠它在开流前拒绝。
    """
    if has_images and llm is None:
        raise ConflictError(
            "chat.llm_disabled",
            "未启用模型,图片无法送进模型:请开启 LLM_ENABLED 后重试",
            details={"mode": mode},
        )


async def decide(
    content: str,
    llm: LLMClient | None,
    *,
    mode: str = AUTO,
    has_images: bool = False,
) -> str:
    """决定这条消息走 chat 还是 task(判别顺序见模块 docstring)。

    has_images 只表示**本条消息**带图片(历史图片不在判定内):任务路径不注入
    图片,所以带图 auto 消息不进模型判别、直接直答。
    """
    if mode == CHAT:
        require_available(mode, llm)
        return CHAT
    require_task_images(mode, has_images)
    if mode == TASK:
        return TASK
    require_image_model(mode, llm, has_images)
    if has_images:
        # 任务路径不注入图片:带图消息只能直答,否则模型根本看不到图
        return CHAT
    if llm is None:
        return TASK
    try:
        reply = await llm.complete(
            [
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format=TRIAGE_RESPONSE_FORMAT,
        )
    except Exception:
        # 判别失败不该阻断消息:退回任务路径(失败也有 run.failed.v1 留痕)
        _log.warning("triage_failed", mode=TASK)
        return TASK
    return _parse_mode(reply.content) or TASK
