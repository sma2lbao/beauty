"""job_* 执行 Mixin 的装配点:把四个职责 Mixin 合成 ToolRunner 使用的一个类。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tool_service.core.config import Settings
from tool_service.services.sandbox import SandboxProvider

from .kill import _JobKillMixin
from .output import _JobOutputMixin
from .start import _JobStartMixin


class JobExecutionMixin(_JobStartMixin, _JobOutputMixin, _JobKillMixin):
    """job_* 的执行实现;由 ToolRunner 多继承装配。"""

    _settings: Settings
    _sandbox: SandboxProvider
    _session_factory: async_sessionmaker[AsyncSession] | None
