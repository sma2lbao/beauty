"""模型正文清洗:去掉"把工具调用标记当正文吐出来"的泄漏段(设计 §6.1)。

轮次用尽后补的那次收束调用是**不带 tools** 的;小模型仍可能把工具调用标记
(`<｜｜DSML｜｜ calls>…`)当正文写出来,落库后成为 run 结果的可见文本。
这里按标记位置切掉泄漏段:调用方拿到可读正文(可能为空,由调用方回退成
确定性小结),事件流里的 delta 已边流边发,不做回溯。
"""

from __future__ import annotations

#: 各家协议泄漏时出现的标记;全角竖线 `｜` 是 DeepSeek 系 `<｜｜…｜｜>` 的专用字符。
LEAK_MARKERS = (
    "<\uff5c\uff5c",
    "<\uff5c",
    "\uff5c\uff5cDSML\uff5c\uff5c",
    "\uff5cDSML\uff5c",
    "\uff5ctool_calls\uff5c",
    "<tool_calls>",
    "<function_calls>",
    "antml:invoke",
    "antml:tool_calls",
)


def strip_tool_markup(text: str) -> str:
    """切掉第一处工具标记及其后的内容;没有标记时原样返回(去尾部空白)。"""

    if not text:
        return ""
    cut = len(text)
    for marker in LEAK_MARKERS:
        index = text.find(marker)
        if index != -1:
            cut = min(cut, index)
    return text[:cut].rstrip()
