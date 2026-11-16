"""沙箱成品回收:执行期写进 /workspace 的成品 → 独立交付物(§7 扩展)。

交付阶段只落报告时,用户拿到的永远是 Markdown:模型执行期写出的小游戏 HTML
只是沙箱里的文件,而工具外链抓取(_capture_tool_files)只看结果里的 http(s)
链接,没有人把沙箱文件读回来。这里按审计记录里的 paths 把**可运行/可直接看**
的成品读回宿主,登记为 source=sandbox 的独立交付物。

注入沿用 hooks 的进程级范式(services/hooks/config.py):容器把沙箱客户端装配
好之后 configure 一个读取器,交付侧只取当前值;未配置就整体跳过(等价于
SANDBOX_ENABLED=false),不碰网络、不改任何服务层函数签名。
"""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agent_orchestrator.context.sandbox_files import WORKSPACE_ROOT

#: 可回收的成品类型:能直接打开/预览/下载的东西。散装脚本(.py/.js)不在此列——
#: 用户要的是"能用的东西",中间产物回收进来只会把交付物列表搅乱。
CONTENT_TYPE_BY_SUFFIX = {
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}

#: 单件上限:交付物还要进工作区镜像与前端预览,超大资产不适合逐件回读。
MAX_BYTES = 5 * 1024 * 1024


class SandboxFileReader(Protocol):
    """交付阶段需要的最小读取能力(实现见 context/sandbox_files.py)。"""

    async def read_bytes(
        self, account_id: str, scope_key: str, path: str
    ) -> bytes | None: ...


_reader: SandboxFileReader | None = None


def configure(reader: SandboxFileReader | None) -> None:
    """装配回收用的沙箱读取器(容器装配沙箱客户端后调用;None = 关闭回收)。"""

    global _reader
    _reader = reader


def reset() -> None:
    """清空读取器(测试隔离用)。"""

    global _reader
    _reader = None


def current() -> SandboxFileReader | None:
    """当前读取器;None 表示本次运行不回收沙箱成品。"""

    return _reader


def workspace_path(path: str) -> str | None:
    """把助手侧路径归一化成沙箱内绝对路径;越狱/空路径返回 None。

    工具信封里的 path 一律是工作区相对路径(helper 自己做过 realpath + 越狱
    校验),但模型在正文里可能写成 /workspace/x.html 或 ./x.html,这里统一成
    <WORKSPACE_ROOT>/rel;工作区外的绝对路径直接拒绝。
    """

    raw = str(path or "").strip()
    if not raw:
        return None
    if raw.startswith(WORKSPACE_ROOT + "/"):
        raw = raw[len(WORKSPACE_ROOT) + 1 :]
    elif raw.startswith(("/", "~")):
        return None
    rel = raw.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return posixpath.join(WORKSPACE_ROOT, *parts)


def content_type_for(filename: str) -> str | None:
    """按扩展名给出可回收件的 content_type;不回收的类型返回 None。"""

    suffix = posixpath.splitext(filename)[1].lower()
    return CONTENT_TYPE_BY_SUFFIX.get(suffix)


def kind_for(content_type: str) -> str:
    """交付物种类:HTML 单独一类,前端据此给"直接打开"的预览,而不是当代码看。

    services/artifacts.py:infer_kind 会把 text/html 归成通配的 text,
    前端就没有可识别的信号;这里显式给 html。
    """

    base = content_type.split(";", 1)[0].strip().lower()
    if base in {"text/html", "application/xhtml+xml"}:
        return "html"
    if base.startswith("image/"):
        return "image"
    return "file"


@dataclass(frozen=True)
class CaptureTarget:
    """一件待回收的沙箱成品。"""

    #: 沙箱内绝对路径(读文件用,也作为去重键)
    path: str
    #: basename(交付物展示名/文件名)
    filename: str
    content_type: str
    #: 交付物种类(html/image/file),落库时直接指定,不走 infer_kind 兜底
    kind: str


def capture_targets(records: Sequence[Any], *, max_files: int) -> list[CaptureTarget]:
    """从工具审计记录里挑出可回收的成品(只看成功调用,去重保序)。

    记录结构见 graph/nodes/audit.py 的 _record:paths 是 write/edit 结果信封里
    的路径列表;失败调用即便带路径也不可信,一律跳过。
    """

    if max_files <= 0:
        return []
    targets: list[CaptureTarget] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("ok", False):
            continue
        paths = record.get("paths")
        if not isinstance(paths, list):
            continue
        for item in paths:
            if not isinstance(item, str):
                continue
            path = workspace_path(item)
            if path is None or path in seen:
                continue
            filename = posixpath.basename(path)
            content_type = content_type_for(filename)
            if content_type is None:
                continue
            seen.add(path)
            targets.append(
                CaptureTarget(
                    path=path,
                    filename=filename,
                    content_type=content_type,
                    kind=kind_for(content_type),
                )
            )
            if len(targets) >= max_files:
                return targets
    return targets
