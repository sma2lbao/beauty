"""知识库、联网、后台作业与交互类工具描述符。"""

from __future__ import annotations

from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

SEARCH_KNOWLEDGE = ToolDescriptor(
    name="search_knowledge",
    description=(
        "在知识库中检索与查询最相关的文档片段(只读)。"
        "返回 query/provider/model 与 hits(标题、相关度、片段内容)。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 2000},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.HTTP,
    timeout_seconds=20.0,
)

FETCH = ToolDescriptor(
    name="fetch",
    description=(
        "用 HTTP GET 抓取公网 URL 的文本内容(只读)。支持 HTML、JSON、XML、"
        "Markdown 与纯文本;不下载图片/二进制;内网、回环与保留地址一律拒绝。"
        "返回 final_url、status、content_type、bytes、truncated 与 text"
        "(超长按字符截断)。GitHub 可用 api.github.com(JSON API)与 "
        "raw.githubusercontent.com(文件原文)读取仓库内容。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "minLength": 8,
                "maxLength": 2048,
                "pattern": "^https?://",
                "description": "要抓取的 http/https URL;可含查询串,不允许用户名/密码。",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.HTTP,
    timeout_seconds=20.0,
)

WEB_SEARCH = ToolDescriptor(
    name="web_search",
    description=(
        "在公网做网页搜索（只读），返回按相关度排序的标题、URL 与摘要。"
        "回答里引用来源时，必须使用结果里的 url 原文写成 markdown 链接，"
        "不要编造或改写 URL；摘要不足以回答时再对具体 url 调 fetch 抓正文。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.HTTP,
    timeout_seconds=20.0,
    enabled=True,
)

JOB_START = ToolDescriptor(
    name="job_start",
    description=(
        "在沙箱里用 /bin/sh -lc 后台启动一条命令并立即返回 job_id,适合训练、"
        "构建、长爬取等长任务;之后用 job_output 查看进度与日志、job_kill 终止。"
        "命令在后台运行,stdout/stderr 落到 /workspace/.tool-jobs/<job_id>/ 下的日志"
        "文件(保留尾部);沙箱默认没有网络。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 4000},
            "cwd": {"type": "string", "minLength": 1, "maxLength": 512},
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    risk=ToolRisk.DESTRUCTIVE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0,
    enabled=True,
)

JOB_OUTPUT = ToolDescriptor(
    name="job_output",
    description=(
        "读取后台作业的输出尾部与状态(running/exited/killed/timeout/lost)。"
        "exited 时退出码真实值,非零也算读取成功;wait_seconds 可在 1~15 秒内"
        "轮询等待状态变化。stdout/stderr 只保留尾部,完整输出请 read 日志文件。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "tail_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 20000,
                "default": 8000,
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 15,
                "default": 0,
            },
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0,
    enabled=True,
)

JOB_KILL = ToolDescriptor(
    name="job_kill",
    description=(
        "终止一个后台作业:先向进程组发 SIGTERM,1 秒后仍存活则 SIGKILL。"
        "已结束的作业不报错(already_finished=true),只回显当前状态与退出码。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk=ToolRisk.WRITE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=20.0,
    enabled=True,
)

ASK_QUESTION = ToolDescriptor(
    name="ask_question",
    description=(
        "向用户提出 1-4 个结构化问题并等待回答。运行时挂起任务并在用户"
        "回答后恢复;工具本身不执行,由编排层处理中断。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1000,
                        },
                        "header": {"type": "string", "minLength": 1, "maxLength": 12},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 80,
                                    },
                                    "description": {
                                        "type": "string",
                                        "maxLength": 300,
                                    },
                                },
                                "required": ["label"],
                                "additionalProperties": False,
                            },
                        },
                        "multi_select": {"type": "boolean", "default": False},
                    },
                    "required": ["question", "options"],
                    "additionalProperties": False,
                },
            },
            "allow_custom": {"type": "boolean", "default": True},
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.INTERACTION,
    executor=ExecutorKind.NONE,
    timeout_seconds=30.0,
    enabled=True,
)
