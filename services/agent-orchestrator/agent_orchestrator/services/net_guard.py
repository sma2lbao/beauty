"""出站地址安全校验(SSRF 收口):私网/回环/链路本地/保留/组播/未指定一律拒绝。

单一真源:地址判定(`is_blocked_address`)与主机名解析(`resolve_host_sync`)在这里
实现一次,`file_capture`(外链下载)与 MCP client(远程 MCP JSON-RPC)共用。

两道防线:

- 注册/连接前(异步、可选 DNS):`assert_url_allowed` 先解析主机名,任一结果命中
  内网即拒绝——覆盖"公网域名解析到 127.0.0.1 / 169.254.169.254"这类绕过;
- 白名单:`allowed_hosts` 里的 host 精确放行(小写比较),给自建内网 MCP 开口子。

解析失败时放行:本次请求根本解析不到目标,真实连接会自己报错,不把 DNS 抖动/禁网
环境变成假阳性拒绝(注意与 `file_capture` 下载口径的区别:下载必须确认目标可达,
所以那边解析失败直接报 artifact.download_failed)。

残余风险:解析与实际连接之间存在 TOCTOU(DNS rebinding),彻底收口需要把连接钉在
已校验的 IP 上(自定义 transport),P0-6 暂不做到这一层。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

from common.logging import get_logger

logger = get_logger(__name__)

#: 字面本机主机名:无需 DNS 就能判定指向本机
LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain"})

#: 运营商级 NAT 共享地址段(100.64.0.0/10):ipaddress 的 is_private 不覆盖它,
#: 但它同样是"内网侧"地址(云上常见于内部服务),SSRF 必须拒绝。
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], list[str]]


class BlockedTargetError(RuntimeError):
    """目标地址命中内网/本机:拒绝出站(SSRF 收口)。"""

    def __init__(
        self,
        message: str,
        *,
        host: str = "",
        addresses: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.host = host
        self.addresses = tuple(addresses)


def is_blocked_address(address: str) -> bool:
    """私网/回环/链路本地/保留/组播/未指定 → True。解析不到具体 IP 也不信。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip in CGNAT_NETWORK
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_host_sync(host: str) -> list[str]:
    """同步解析主机名(调用方放线程里跑,避免阻塞事件循环)。"""
    infos = socket.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def parse_allowed_hosts(hosts: Sequence[str]) -> tuple[str, ...]:
    """白名单归一:去空白、小写、去空项(调用方通常直接传配置里的列表)。"""
    return tuple(host.strip().lower() for host in hosts if host and host.strip())


def _is_localhost_name(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return normalized in LOCALHOST_NAMES or normalized.endswith(".localhost")


def _literal_address(host: str) -> str | None:
    """主机名本身是字面 IP(含 IPv6 方括号已由 urlparse 去掉)时返回它。"""
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


def url_host(url: str) -> str:
    """取 URL 主机名(小写,空串表示没有主机名)。"""
    return (urlparse(url).hostname or "").strip().lower()


def _blocked_message(host: str, addresses: Sequence[str]) -> str:
    detail = ", ".join(addresses) if addresses else "本机地址"
    return (
        f"目标地址指向内网,已拒绝连接:{host}(命中 {detail})。"
        "如确需访问内网 MCP server,请把它加入 MCP_ALLOWED_HOSTS 白名单。"
    )


async def assert_url_allowed(
    url: str,
    *,
    allowed_hosts: Sequence[str] = (),
    resolver: Resolver | None = None,
) -> None:
    """请求前校验 URL 主机:白名单放行,字面 IP 直判,否则解析后逐地址判定。"""
    host = url_host(url)
    if not host:
        raise BlockedTargetError(f"URL 缺少主机名,已拒绝连接:{url}", host=host)
    if host in parse_allowed_hosts(allowed_hosts):
        return
    literal = _literal_address(host)
    if literal is not None:
        if is_blocked_address(literal):
            raise BlockedTargetError(
                _blocked_message(host, [literal]), host=host, addresses=[literal]
            )
        return
    if _is_localhost_name(host):
        raise BlockedTargetError(_blocked_message(host, []), host=host)
    try:
        addresses = (
            list(resolver(host))
            if resolver is not None
            else await asyncio.to_thread(resolve_host_sync, host)
        )
    except Exception as exc:  # noqa: BLE001 - 解析不到就交给真实连接报错
        logger.debug(
            "url guard resolution failed; allowing request",
            host=host,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    blocked = [address for address in addresses if is_blocked_address(address)]
    if blocked:
        raise BlockedTargetError(
            _blocked_message(host, blocked), host=host, addresses=blocked
        )
