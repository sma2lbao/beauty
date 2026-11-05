"""鉴权替身:本地 TestIdP 与 Bearer 头助手。

- TestIdP 由 common.testing 提供:进程内本地密钥,JWKS 走 MockTransport;
- 测试请求一律带 Bearer(账号在 claim 里),不再用 X-Account-Id 临时头(架构 §10);
- 契约由 duck typing 满足(见 services/llm.py、services/knowledge.py 的 Protocol)。
"""

from __future__ import annotations

from common.testing import TestIdP

_IDP = TestIdP()
ACCOUNT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def bearer(account: str) -> dict[str, str]:
    """按账号签 Bearer 头(测试显式指明调用方账号)。"""
    return _IDP.bearer(account)


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()
