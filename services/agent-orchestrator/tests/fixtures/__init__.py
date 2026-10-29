"""测试夹具包:按主题拆分的共享 fakes 与装配助手。

模块划分:
- ``auth``:TestIdP/JWKS 校验与 Bearer 头助手;
- ``llm``:FakeLLM;
- ``knowledge``:FakeKnowledge;
- ``workspace``:FakeWorkspace;
- ``outbox``:RecordingBus;
- ``tools``:FakeDangerTool/ask_question schema/FakeInteractionToolkit;
- ``app``:测试 settings、client 夹具与应用装配助手。

夹具经 tests/conftest.py 导入注册,名字与可见性(含 autouse)与原 conftest 一致。
"""
