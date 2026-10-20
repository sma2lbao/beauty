"""通用二进制对象契约(P3):skill 包等不进 workspace_artifacts 表。

请求侧只做硬上限校验;真正的字节上限由配置 `object_max_bytes`
(默认 20MB)决定——配置应比这里的 Base64 硬上限更保守。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_CONTENT_TYPE = "application/octet-stream"
MAX_FILENAME_LENGTH = 255
MAX_CONTENT_TYPE_LENGTH = 120
MAX_KEY_LENGTH = 512
# Base64 字符串硬上限(防超大请求体先撑爆内存),约合 48MB 原始字节;
# 业务上限以 settings.object_max_bytes 为准(默认 20MB)。
MAX_BASE64_CHARS = 64_000_000


class ObjectCreate(BaseModel):
    """上传对象:内容用 Base64;filename 只用于安全后缀,不参与路径。"""

    filename: str | None = Field(default=None, max_length=MAX_FILENAME_LENGTH)
    content_type: str = Field(
        default=DEFAULT_CONTENT_TYPE,
        min_length=1,
        max_length=MAX_CONTENT_TYPE_LENGTH,
    )
    content_base64: str = Field(max_length=MAX_BASE64_CHARS)


class ObjectMeta(BaseModel):
    """写入回执:定位对象所需的最小信息。"""

    key: str
    size_bytes: int
    sha256: str


class ObjectRead(ObjectMeta):
    """读取结果:元信息 + Base64 正文(size/sha256 均对解码后字节计算)。"""

    content_base64: str
