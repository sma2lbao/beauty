"""MCP Client 契约(M1 本地版 + P2 stdio)。

两种传输的输入约束在这里**一次声明**,服务层 create/update 复用同一套校验:

- http:url 必填且 http(s);command/args/env/image 不允许出现(runtime 只能是
  默认的 python——http 没有"进程运行时"概念);
- stdio:command 必填且只允许 `[A-Za-z0-9._/-]`(挡掉 shell 元字符);url/token
  必须为空;env 只接受 POSIX 变量名、最多 50 项、单值 ≤2000 字符;args 最多
  50 项、单项 ≤500 字符;runtime 只能是 python|node。

读端**永不返回** token/env 值:只回 has_token/token_hint 与 has_env/env_keys。

`auto_approve`(免确认执行,默认 False)是租户对该 server 的信任声明:对话路径
(chat)不产生审批流,只有置 True 的工具才会被真正执行(§6.4 策略配置化)。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

#: stdio 命令:只允许可执行名 + 相对路径,禁止空格/引号/管道等 shell 元字符
COMMAND_PATTERN = r"^[A-Za-z0-9._/-]+$"
#: env 变量名(POSIX):非法名字会被下游 shell/进程静默丢弃,这里直接拒
ENV_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
HTTP_URL_PATTERN = re.compile(r"^https?://\S+$")
MAX_ARGS = 50
MAX_ARG_LENGTH = 500
MAX_ENV_ITEMS = 50
MAX_ENV_VALUE_LENGTH = 2000

Transport = Literal["http", "stdio"]
Runtime = Literal["python", "node"]


class MCPToolRead(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    transport: Transport = "http"
    # http:必填;stdio:必须为空
    url: str | None = Field(default=None, max_length=500)
    # 只给 http 用;stdio 必须为空
    token: str | None = Field(default=None, max_length=4096)
    command: str | None = Field(default=None, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=MAX_ARGS)
    env: dict[str, str] = Field(default_factory=dict)
    runtime: Runtime = "python"
    image: str | None = Field(default=None, max_length=120)
    enabled: bool = True
    # 免确认执行:信任该 server(默认关,写操作仍在任务里逐次确认)
    auto_approve: bool = False
    tools_allowlist: list[str] = Field(default_factory=list, max_length=100)
    timeout_seconds: int = Field(default=10, ge=1, le=120)

    @field_validator("args")
    @classmethod
    def _check_args(cls, value: list[str]) -> list[str]:
        for arg in value:
            if len(arg) > MAX_ARG_LENGTH:
                raise ValueError(f"args 单项不能超过 {MAX_ARG_LENGTH} 字符")
        return value

    @field_validator("env")
    @classmethod
    def _check_env(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_ENV_ITEMS:
            raise ValueError(f"env 最多 {MAX_ENV_ITEMS} 项")
        for key, item in value.items():
            if not re.fullmatch(ENV_KEY_PATTERN, key):
                raise ValueError(f"env 变量名非法:{key}")
            if len(item) > MAX_ENV_VALUE_LENGTH:
                raise ValueError(
                    f"env 变量 {key} 的值不能超过 {MAX_ENV_VALUE_LENGTH} 字符"
                )
        return value

    @model_validator(mode="after")
    def _check_transport(self) -> MCPServerCreate:
        if self.transport == "stdio":
            self._check_stdio()
        else:
            self._check_http()
        return self

    def _check_http(self) -> None:
        url = (self.url or "").strip()
        if not url:
            raise ValueError("http 传输必须提供 url")
        if not HTTP_URL_PATTERN.match(url):
            raise ValueError("url 必须是 http(s):// 开头的地址")
        if self.command is not None or self.args or self.env or self.image:
            raise ValueError(
                "http 传输不接受 command/args/env/image(请改用 transport=stdio)"
            )
        if self.runtime != "python":
            raise ValueError("http 传输没有运行时概念,runtime 只能是 python")

    def _check_stdio(self) -> None:
        if not self.command:
            raise ValueError("stdio 传输必须提供 command")
        if not re.fullmatch(COMMAND_PATTERN, self.command):
            raise ValueError("command 只允许字母数字与 . _ / -")
        if (self.url or "").strip() or (self.token or "").strip():
            raise ValueError("stdio 传输不接受 url/token")
        if self.runtime not in ("python", "node"):
            raise ValueError("stdio 传输的 runtime 只能是 python 或 node")


class MCPServerUpdate(BaseModel):
    """PATCH 载荷:字段全可选;跨字段约束由服务层合并现有值后统一校验。"""

    name: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"
    )
    transport: Transport | None = None
    url: str | None = Field(default=None, max_length=500)
    token: str | None = Field(default=None, max_length=4096)
    command: str | None = Field(default=None, max_length=200)
    args: list[str] | None = Field(default=None, max_length=MAX_ARGS)
    env: dict[str, str] | None = None
    runtime: Runtime | None = None
    image: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    auto_approve: bool | None = None
    tools_allowlist: list[str] | None = Field(default=None, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)


class MCPServerRead(BaseModel):
    id: str
    name: str
    transport: str
    url: str  # stdio 恒为空字符串
    command: str | None
    args: list[str]
    runtime: str
    image: str | None
    enabled: bool
    auto_approve: bool
    has_token: bool
    token_hint: str | None
    has_env: bool
    env_keys: list[str]  # 只有 key 名,绝不含值
    tools_allowlist: list[str]
    tools: list[MCPToolRead]
    timeout_seconds: int
    #: ok | error | auth_required(最后一次测试/调用时服务端回 401)
    last_status: str | None
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime
    #: 浏览器 OAuth 授权(P1):是否已建立客户端、是否持有可用 token
    has_oauth: bool = False
    oauth_authorized: bool = False
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_expires_at: datetime | None = None
    oauth_authorization_server: str | None = None
    #: 最近一次授权/刷新失败原因(不含 token;重新授权成功即清空)
    oauth_error: str | None = None


class MCPTestRead(BaseModel):
    ok: bool
    detail: str
    tools: list[MCPToolRead]
    #: 服务端要求(重新)授权(HTTP 401):前端据此给出"重新授权"指引,而不是笼统的失败
    auth_required: bool = False


class MCPOAuthStartRead(BaseModel):
    #: 浏览器需要跳转的授权地址(含 PKCE 与一次性 state)
    authorize_url: str
    #: state 有效期(秒):前端可据此提示"链接过期请重试"
    expires_in: int


class MCPOAuthCallbackRequest(BaseModel):
    """BFF → orchestrator 内部回调契约(经 X-Internal-Secret 鉴权)。"""

    state: str = Field(min_length=1, max_length=8192)
    code: str = Field(min_length=1, max_length=8192)


class MCPOAuthCallbackRead(BaseModel):
    #: 换 token 是否成功;失败时 redirect_path 仍指向 /mcp?oauth=error
    ok: bool
    #: 前端要跳转的站内相对路径(由 orchestrator 统一拼,避免 BFF 了解错误文案)
    redirect_path: str
    error: str | None = None
