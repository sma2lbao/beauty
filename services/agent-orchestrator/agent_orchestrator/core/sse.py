"""SSE 帧编码:纯函数集合,只拼字符串,不碰连接/事务/状态。

三条帧格式(逐字节契约,客户端解析见 apps/web 的 sse.ts):

- 具名事件:`event: <name>\ndata: <JSON>\n\n`(conversations 的
  meta/delta/tool/done/error;notifications 的 run.status_changed.v1 还带 id);
- run 事件:`id: <seq>\nevent: <type>\ndata: <JSON>\n\n`——id 在前,客户端据此
  在断线重连时回传 Last-Event-ID 续传;
- 注释帧:以冒号开头的 `: <text>\n\n`,SSE 规范要求客户端跳过;用于 keep-alive
  与"已就绪"提示,不承载数据。

MCP Streamable HTTP 的 data-only 帧是另一套传输契约(见 api/mcp_server.py),
不在此列——这里只服务控制台的浏览器事件流。
"""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.models.runs import RunEvent
from agent_orchestrator.schemas.runs import RunEventRead


def comment_frame(text: str) -> str:
    """注释帧(keep-alive / ready):客户端必须跳过,不出现在 onFrame 回调里。"""
    return f": {text}\n\n"


def named_event_frame(name: str, data: dict[str, Any]) -> str:
    """具名事件帧:前端按 event 名分发,data 为 JSON(中文不转义)。"""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def event_id_frame(seq: int, event_type: str, data: dict[str, Any]) -> str:
    """带游标的具名事件帧(id 在前,供 Last-Event-ID 续传)。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"id: {seq}\nevent: {event_type}\ndata: {payload}\n\n"


def run_event_frame(event: RunEvent) -> str:
    """一条 run 事件 → SSE 帧(契约:游标 = seq,data 用 schema 的 JSON 序列化)。"""
    data = RunEventRead.model_validate(event).model_dump_json()
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


def run_end_frame(status: str) -> str:
    """stream.end:终态收尾帧,不带 id(不可回放,客户端停重连)。"""
    return f"event: stream.end\ndata: {json.dumps({'status': status})}\n\n"
