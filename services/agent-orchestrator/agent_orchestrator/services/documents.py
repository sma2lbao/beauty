"""文档域(双职责):本地文档库 + 附件文本提取。

附件文本提取段(extract_text/DocumentParseError)原属 common.documents,
2026-10 移交本服务(全仓唯一使用方是 attachments.py),解析依赖
pypdf/python-docx 也随迁到本服务 pyproject 声明。

本地文档库(§8 技能来源):只读、目录受限的文档访问。

约定:

- 一切路径都相对 `root`,解析后必须仍在 root 内(防目录穿越);
- 只允许文本类后缀(`ALLOWED_SUFFIXES`),超限文档拒绝读取(有 max_bytes 上限);
- 文件 IO 走 `asyncio.to_thread`;故障是 500(`StorageError`)。

阶段 B 接 S3/Drive 时,把本类换成同一套方法签名的实现即可(工具层不动)。
"""

from __future__ import annotations

import contextlib
import re
from asyncio import to_thread
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from common.errors import StorageError

ALLOWED_SUFFIXES = (".md", ".txt", ".csv", ".json", ".log", ".rst")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._\-\u4e00-\u9fff/]+$")


@dataclass(frozen=True)
class DocumentInfo:
    """文档条目:相对路径 + 体量 + 修改时间。"""

    path: str
    size_bytes: int
    modified_at: str


class DocumentLibrary:
    """受限目录内的文档只读访问。"""

    def __init__(self, root: str | Path, max_bytes: int = 200_000) -> None:
        self._root = Path(root).expanduser().resolve()
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    async def list(self, *, limit: int = 50) -> list[DocumentInfo]:
        """列出允许的文档(按相对路径排序),最多 limit 条。"""
        return await to_thread(self._list_sync, limit)

    async def read(self, relative_path: str) -> str:
        """读取文档正文(UTF-8);越界/后缀不允许/超限都会拒绝。"""
        path = self._resolve(relative_path)
        try:
            data = await to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(
                "document.not_found", f"文档不存在:{relative_path}"
            ) from exc
        except OSError as exc:
            raise StorageError("document.read_failed", f"读取文档失败:{exc}") from exc
        if len(data) > self._max_bytes:
            raise StorageError(
                "document.too_large",
                f"文档超过 {self._max_bytes} 字节上限",
                details={"path": relative_path, "size_bytes": len(data)},
            )
        return data.decode("utf-8", "replace")

    def _list_sync(self, limit: int) -> list[DocumentInfo]:
        if not self._root.exists():
            return []
        entries: list[DocumentInfo] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:  # 竞态:文件刚被删除
                continue
            entries.append(
                DocumentInfo(
                    path=str(path.relative_to(self._root)),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                )
            )
            if len(entries) >= limit:
                break
        return entries

    def _resolve(self, relative_path: str) -> Path:
        text = str(relative_path).strip()
        # 绝对路径与反斜杠路径直接拒绝:不静默改写成相对路径
        if (
            not text
            or text.startswith(("/", "\\"))
            or "\\" in text
            or not _SAFE_NAME.match(text)
        ):
            raise StorageError(
                "document.invalid_path", f"非法文档路径:{relative_path!r}"
            )
        candidate = (self._root / text).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise StorageError(
                "document.invalid_path", f"文档路径越出库根:{relative_path!r}"
            )
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            raise StorageError(
                "document.unsupported_type",
                f"不支持的文档类型:{candidate.suffix or '(无后缀)'}",
                details={"allowed": list(ALLOWED_SUFFIXES)},
            )
        return candidate


# ── 附件文本提取(原 common.documents 移交段) ─────────────────────────────
# 全仓唯一使用方是本服务 attachments.py;解析依赖(pypdf/python-docx/openpyxl)
# 由本服务 pyproject 声明,不再由 common 传递给全部服务。

MAX_CHARS = 20_000

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}
_PDF_SUFFIXES = {".pdf"}
_DOCX_SUFFIXES = {".docx"}
_XLSX_SUFFIXES = {".xlsx", ".xlsm"}


class DocumentParseError(ValueError):
    """附件无法解析为文本(格式不支持/损坏/加密)。"""


def _suffix(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def extract_text(
    filename: str,
    content_type: str | None,
    data: bytes,
    *,
    max_chars: int = MAX_CHARS,
) -> str:
    """附件文本提取:上传时解析注入 Agent 上下文。

    解析失败抛 DocumentParseError,由调用方决定"记录失败但保留附件"
    还是拒绝。输出统一截断到 max_chars,避免把整本 PDF 塞进提示词。
    """
    suffix = _suffix(filename)
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if suffix in _TEXT_SUFFIXES or kind.startswith("text/"):
        text = data.decode("utf-8", "replace")
    elif suffix in _PDF_SUFFIXES or kind == "application/pdf":
        text = _pdf_text(data)
    elif suffix in _DOCX_SUFFIXES or kind in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        text = _docx_text(data)
    elif suffix in _XLSX_SUFFIXES or kind in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        text = _xlsx_text(data)
    else:
        raise DocumentParseError(f"不支持解析的附件类型:{suffix or kind or '未知'}")
    text = text.replace("\x00", "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(内容已截断)"
    return text


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖缺失时明确报错
        raise DocumentParseError("PDF 解析依赖未安装") from exc
    try:
        reader = PdfReader(BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"PDF 解析失败:{exc}") from exc
    return "\n\n".join(pages)


def _docx_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise DocumentParseError("docx 解析依赖未安装") from exc
    try:
        document = Document(BytesIO(data))
        lines = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append("\t".join(cell.text for cell in row.cells))
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"docx 解析失败:{exc}") from exc
    return "\n".join(lines)


def _xlsx_text(data: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise DocumentParseError("xlsx 解析依赖未安装") from exc
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in workbook.worksheets:
            lines.append(f"## Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    lines.append("\t".join(values))
    except Exception as exc:  # noqa: BLE001
        raise DocumentParseError(f"xlsx 解析失败:{exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            workbook.close()  # type: ignore[possibly-undefined]
    return "\n".join(lines)
