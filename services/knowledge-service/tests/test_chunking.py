"""分块:段落优先、长段窗口切分(重叠)、空文本与非法参数。"""

import pytest

from knowledge.services.chunking import chunk_text


def test_short_paragraphs_merge_until_size() -> None:
    text = "第一段内容\n\n第二段内容\n\n第三段内容"
    chunks = chunk_text(text, size=15, overlap=5)
    # 前两段合起来 12 字符 ≤15;再加第三段是 19 >15 → 另起一块
    assert len(chunks) == 2
    assert chunks[0] == "第一段内容\n\n第二段内容"
    assert chunks[1] == "第三段内容"


def test_long_paragraph_splits_with_overlap() -> None:
    text = "阿" * 25
    chunks = chunk_text(text, size=10, overlap=2)
    # step = 8 → 窗口起点 0/8/16;末尾 1 字符已被前一块覆盖 → 丢弃
    assert len(chunks) == 3
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert chunks[0][-2:] == chunks[1][:2]  # 相邻块重叠 2 字符
    assert chunks[2][-1] == "阿"  # 尾部内容不丢


def test_empty_and_whitespace_return_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \n") == []


def test_normalizes_excess_blank_lines() -> None:
    chunks = chunk_text("甲\n\n\n\n乙", size=50)
    assert chunks == ["甲\n\n乙"]


def test_invalid_size_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", size=0)
