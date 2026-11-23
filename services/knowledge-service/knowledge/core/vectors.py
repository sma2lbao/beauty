"""向量维度与列类型:pgvector 的唯一真源(迁移、模型、嵌入提供方共用)。

维度是**代码常量而不是环境变量**:改维度必须同时做列类型迁移 + 全量重嵌入,
把它做成 env 只会让"配置改了、库里还是旧维度"这种错误在运行时才炸。
换模型(维度不同)的正确姿势是新迁移 + 重嵌入,
见 docs/design/knowledge-and-memory-upgrade.md 的 K5。
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector

#: 向量维度:与默认网关模型 bge-m3(1024 维)对齐;本地哈希提供方也按此维度输出,
#: 这样 local/gateway 切换时列类型不需要变(否则 HNSW 索引会因维度不一致失效)。
EMBEDDING_DIM = 1024


def embedding_vector_type() -> Vector:
    """分块向量的列类型:Postgres 上是 `vector(1024)`,可建 HNSW 索引。

    SQLite(测试/离线覆盖库)没有 vector 类型,但 SQLAlchemy 会把它当自定义类型
    声明、按 text 存取——本类型自带的 bind/result 处理器仍能读写 list[float],
    所以测试链路不需要第二套模型。
    """
    return Vector(EMBEDDING_DIM)
