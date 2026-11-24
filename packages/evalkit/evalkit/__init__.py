"""evalkit:面型 monorepo 的离线 Agent/LLM 评测平台。

用例是数据(JSONL),adapter 负责驱动被测目标,grader 负责评分,基线负责
行为漂移门禁。核心只依赖 Python 标准库,便于在任何服务/语言边界复用。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
