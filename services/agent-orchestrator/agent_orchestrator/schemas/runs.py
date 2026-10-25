"""对外 DTO(pydantic)。与 ORM 模型分离:传输契约不泄漏存储细节。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """run 状态机(架构 §6.2):goal 语义,blocked 可 resume。"""

    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BLOCKED = "blocked"
    # 取消(P0-2):任意非终态都可取消;执行中的图调用不强制打断,
    # 执行边界回来时读到 cancelled 就不再覆盖终态(协作式取消)。
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class RunCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    # 对话归属:由对话消息触发的 run 带;控制台直建为空
    conversation_id: str | None = Field(default=None, min_length=1, max_length=32)
    # 工作区归属:不传则从所属对话继承(直建 run 为空)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=32)
    # 可选 Skill(M1):创建时快照 manifest,plan/execute 注入约束与工具白名单
    skill_id: str | None = Field(default=None, max_length=80)
    # 显式引用的 MCP server:非空时只装配这些 server 的工具(空 = 全部已启用)
    mcp_server_ids: list[str] = Field(default_factory=list, max_length=10)


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    goal: str
    status: RunStatus
    rounds: int
    attempts: int
    acceptance_criteria: list[str]
    status_reason: str | None
    parent_run_id: str | None = None
    conversation_id: str | None = None
    workspace_id: str | None = None
    skill_id: str | None = None
    mcp_server_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: str


class StepRead(BaseModel):
    """run 步骤视图(S1):run.step.v1 的事件投影,不是表(07-run-step-model.md)。

    node/phase 是步骤归属的节点与阶段;round 是该 (attempt, node) 的第几次执行;
    started_at/finished_at/duration_ms 给出可定位的时间线。
    """

    seq: int
    step_id: str
    node: str
    phase: str
    attempt: int
    round: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None


class PlanUpdate(BaseModel):
    """审批前编辑计划(§6.1:plan 是可编辑工件)。"""

    plan_markdown: str = Field(min_length=1, max_length=20000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(BaseModel):
    """写操作确认(§6.2):放行则执行,拒绝则跳过并继续验收。"""

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class CancelRequest(BaseModel):
    """取消 run(P0-2):reason 可选,落 run.cancelled.v1 与 status_reason。"""

    reason: str | None = Field(default=None, max_length=2000)


class InstructionCreate(BaseModel):
    """运行中追加指令(§6.5):在下一个图边界(规划/放行/确认)被消费。"""

    content: str = Field(min_length=1, max_length=2000)


class InstructionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    content: str
    status: str
    created_at: datetime


class PlanRead(BaseModel):
    run_id: str
    status: RunStatus
    plan_markdown: str


class ArtifactRead(BaseModel):
    """交付物读取契约(§7):内容已装载,附存储后端与体量。

    二进制交付物(截图/PDF 等)content 为空串,按 `content_type` 调用下载端点取字节。
    """

    id: str
    run_id: str
    kind: str
    title: str
    content: str
    storage: str
    size_bytes: int
    created_at: datetime
    # 文件名/内容类型/来源:旧客户端不传也能解析(有默认)
    filename: str | None = None
    content_type: str | None = None
    source: str = "report"


class ArtifactMetaRead(BaseModel):
    """交付物清单项(不含正文):列表页/下载入口用。"""

    id: str
    run_id: str
    kind: str
    title: str
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int
    storage: str
    source: str = "report"
    created_at: datetime
