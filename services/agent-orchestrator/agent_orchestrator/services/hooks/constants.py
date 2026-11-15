"""钩子服务的常量:取值上限、头部名、控制字符与目标校验错误。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from common.errors import AppError

from agent_orchestrator.services import net_guard

#: 日志器名称:包内各模块共用,保持拆分前的 logger 名不变。
LOGGER_NAME = "agent_orchestrator.services.hooks"
#: 请求体里 arguments/result 的截断长度(§4.1,尾部标注 "(truncated)")。
ARGUMENT_CHARS = 8000
#: 响应里 reason/note 的截断长度(§4.2);note 追加进模型可见内容前统一清洗。
REASON_CHARS = 512
NOTE_CHARS = 1000
#: 响应体上限(§4.3),超出丢弃并按失败处理(fail-open)。
RESPONSE_MAX_BYTES = 16 * 1024
#: 单条 hook 超时上限;实际取 min(本列, HOOKS_TIMEOUT_SECONDS, 30)。
MAX_TIMEOUT_SECONDS = 30.0
#: 协议头(§4.1)。
EVENT_HEADER = "X-DSH-Hook-Event"
ID_HEADER = "X-DSH-Hook-Id"
TIMESTAMP_HEADER = "X-DSH-Timestamp"
SIGNATURE_HEADER = "X-DSH-Signature"
#: secret 生成长度(token_urlsafe(32) ≈ 43 字符,列宽 128 够用)。
SECRET_BYTES = 32

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InvalidHookTarget(AppError):
    """hook target 非法(非 https / 私网地址 / DNS 解析失败)→ 422。"""

    status = 422


@dataclass(frozen=True, slots=True)
class HookConfig:
    """进程级 hooks 配置(容器装配一次;runner 每次调用时读当前值)。"""

    enabled: bool = False
    timeout_seconds: float = 3.0
    fail_closed: bool = False
    max_per_account: int = 20
    allow_http: bool = False
    allowed_hosts: tuple[str, ...] = ()
    #: 测试注入:MockTransport 与假 resolver(生产为 None = 真实网络/DNS)。
    transport: httpx.AsyncBaseTransport | None = None
    resolver: net_guard.Resolver | None = None
