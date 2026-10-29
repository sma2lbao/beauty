"""测试装配入口:共享 fakes(FakeLLM/FakeKnowledge/FakeDangerTool)与应用装配助手。

fixture 实现已按主题迁到 ``tests/fixtures/``(auth/llm/knowledge/workspace/outbox/
tools/app),本文件只负责两件事:

1. **注册 fixture**:把 fixtures 模块里的 fixture(含 autouse 的进程级复位夹具与
   ``client``)导入本模块命名空间——pytest 按 conftest 命名空间扫描 fixture,
   因此全部原 fixture 名字与可见性保持不变;
2. **重导出符号**:大量测试与 tests/support 模块用 ``from tests.conftest import X``
   取 Fake*/常量/助手,这里原样转出,历史 import 路径继续可用。

约定(与拆分前一致):
- 每个测试独立 sqlite 文件库,建表走 lifespan(auto_create_tables);
- LLM 与工具全部注入 fake:零网络、零 token;
- 鉴权开且验真 token(架构 §10):TestIdP 本地密钥,JWKS 走 MockTransport,
  测试请求一律带 Bearer(租户在 claim 里),不再用 X-Tenant-Id 临时头;
- 契约由 duck typing 满足(见 services/llm.py、services/knowledge.py 的 Protocol)。
"""

from __future__ import annotations

from tests.fixtures.app import (
    _SETTINGS_DEFAULTS,
    _client,
    _create_run,
    _reset_models_cache,
    _reset_process_budget,
    _settings,
    _tool_client,
    client,
)
from tests.fixtures.auth import _IDP, OTHER, TENANT, bearer, verifier
from tests.fixtures.knowledge import FakeKnowledge
from tests.fixtures.llm import FakeLLM
from tests.fixtures.outbox import RecordingBus
from tests.fixtures.tools import (
    ASK_QUESTION_SCHEMA,
    DEFAULT_ASK_QUESTIONS,
    FakeDangerTool,
    FakeInteractionToolkit,
    ask_reply,
)
from tests.fixtures.workspace import FakeWorkspace

__all__ = [
    "ASK_QUESTION_SCHEMA",
    "DEFAULT_ASK_QUESTIONS",
    "OTHER",
    "TENANT",
    "_IDP",
    "_SETTINGS_DEFAULTS",
    "_client",
    "_create_run",
    "_reset_models_cache",
    "_reset_process_budget",
    "_settings",
    "_tool_client",
    "FakeDangerTool",
    "FakeInteractionToolkit",
    "FakeKnowledge",
    "FakeLLM",
    "FakeWorkspace",
    "RecordingBus",
    "ask_reply",
    "bearer",
    "client",
    "verifier",
]
