"""文本分块(§9):段落边界优先,长段落按窗口硬切并保留重叠。

刻意保持简单可解释:阶段 A 不做语义分块(标题层级/表格感知)——
分块策略变化只影响 ingest,检索读端契约不变。
"""

from __future__ import annotations

import re


def chunk_text(text: str, *, size: int = 400, overlap: int = 60) -> list[str]:
    """切成 ≤size 字符的块;空文本返回空列表。"""
    if size <= 0:
        raise ValueError("size 必须为正数")
    step = max(1, size - max(0, overlap))
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not normalized:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in (part.strip() for part in normalized.split("\n\n")):
        if not paragraph:
            continue
        for piece in _split_long(paragraph, size=size, step=step):
            if not current:
                current = piece
            elif len(current) + 2 + len(piece) <= size:
                current = f"{current}\n\n{piece}"
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(text: str, *, size: int, step: int) -> list[str]:
    """单段超过 size 时按滑动窗口切分(相邻块重叠 = size - step)。

    末尾窗口若不长于重叠长度,说明它的内容已被前一块完全覆盖——
    丢弃以免产生一两个字符的碎片块(检索噪声)。
    """
    if len(text) <= size:
        return [text]
    overlap = size - step
    pieces = [text[index : index + size] for index in range(0, len(text), step)]
    if len(pieces) > 1 and len(pieces[-1]) <= overlap:
        pieces.pop()
    return [piece for piece in pieces if piece.strip()]
