"""测试专用 demo provider:验证平台链路的假上游,不进入生产代码。

用户身份 = 固定 OAuth 客户端 + 一次性 refresh token 语义(与真实 SaaS 同构);
上游端点都在 api.example.test / auth.example.test,由 conftest 的 DemoMock 打桩,
零网络。这里不是任何真实产品的实现:平台本身不内置 provider。

类本体只保留 provider 清单与生命周期钩子;授权/身份链路见 demo_provider_auth,
工具清单与执行见 demo_provider_tools,上游 HTTP 与错误映射见 demo_provider_http。
"""

from __future__ import annotations

from typing import ClassVar

from connector.core.config import Settings
from connector.providers.base import BaseProvider, ToolSpec
from tests.demo_provider_auth import _AuthMixin
from tests.demo_provider_http import _HttpMixin
from tests.demo_provider_tools import TOOLS, _ToolsMixin


class DemoProvider(_AuthMixin, _ToolsMixin, _HttpMixin, BaseProvider):
    name: ClassVar[str] = "demo"
    prefix: ClassVar[str] = "Demo"
    display_name: ClassVar[str] = "示例连接器"
    description: ClassVar[str] = "测试与本地联调用的示例只读连接器"
    docs_url: ClassVar[str] = "https://example.test/docs"
    auth_kind: ClassVar[str] = "user"
    # 平台能力:允许用户粘贴静态令牌建连接(OAuth 之外的第二条路径)
    token_connectable: ClassVar[bool] = True
    # 应用凭证默认值;测试可通过改成空串模拟「未配置」。
    DEFAULT_APP_ID: ClassVar[str] = "demo-client"
    DEFAULT_APP_SECRET: ClassVar[str] = "demo-secret"
    SCOPES: ClassVar[tuple[str, ...]] = ("offline_access", "docs:readonly")
    AUTH_BASE: ClassVar[str] = "https://auth.example.test"
    API_BASE: ClassVar[str] = "https://api.example.test"

    tools: ClassVar[tuple[ToolSpec, ...]] = TOOLS

    def __init__(
        self,
        settings: Settings,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
    ) -> None:
        super().__init__(settings)
        cls = type(self)
        self._app_id = app_id if app_id is not None else cls.DEFAULT_APP_ID
        self._app_secret = (
            app_secret if app_secret is not None else cls.DEFAULT_APP_SECRET
        )

    def with_account_app(self, app_id: str, app_secret: str) -> DemoProvider:
        return DemoProvider(self.settings, app_id=app_id, app_secret=app_secret)

    @property
    def configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    def scopes(self) -> tuple[str, ...]:
        return self.SCOPES
