"""正文清洗:轮次用尽后的收束调用若把工具标记当正文吐出来,不能落成结果文本。"""

from agent_orchestrator.graph.reply_clean import strip_tool_markup


def test_strip_tool_markup_keeps_clean_text() -> None:
    assert strip_tool_markup("# 小结\n已写出 snake.html") == "# 小结\n已写出 snake.html"


def test_strip_tool_markup_cuts_dsml_leak() -> None:
    leaked = (
        "已读取 snake.html。\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
        '<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read_offloaded">\n'
        '<\uff5c\uff5cDSML\uff5c\uff5c parameter name="path">/workspace/.agent/x.txt'
    )
    assert strip_tool_markup(leaked) == "已读取 snake.html。"


def test_strip_tool_markup_cuts_xml_style_leak() -> None:
    assert strip_tool_markup('正文\n<tool_calls>{"name": "read"}') == "正文"


def test_strip_tool_markup_handles_empty_and_pure_leak() -> None:
    assert strip_tool_markup("") == ""
    assert strip_tool_markup("<tool_calls>[{...}]") == ""
