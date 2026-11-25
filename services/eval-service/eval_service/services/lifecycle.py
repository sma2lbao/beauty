"""COS 桶生命周期策略:给报告对象加一道兜底过期规则。

保留期清理会先删 DB 行与对象；桶生命周期用于覆盖清理失败/进程中断留下的
孤儿对象。默认 dry-run，只有 --apply 才真正写桶配置。
"""

from __future__ import annotations

import asyncio
from typing import Any

from eval_service.core.config import Settings
from eval_service.core.storage import build_cos_client

LIFECYCLE_RULE_ID = "eval-report-expiry"
# 未完成分片上传 1 天后清理，避免大报告上传中断长期占空间。
ABORT_MULTIPART_DAYS = 1


def build_lifecycle_policy(days: int, prefix: str) -> dict[str, Any]:
    """构造 COS PutBucketLifecycle 规则；days/prefix 非法时直接拒绝。"""

    if days < 1:
        raise ValueError("生命周期天数必须大于 0")
    clean_prefix = prefix.strip()
    if not clean_prefix:
        raise ValueError("生命周期前缀不能为空")
    return {
        "Rule": [
            {
                "ID": LIFECYCLE_RULE_ID,
                "Filter": {"Prefix": clean_prefix},
                "Status": "Enabled",
                "Expiration": {"Days": days},
                "AbortIncompleteMultipartUpload": {
                    "Days": ABORT_MULTIPART_DAYS,
                },
            }
        ]
    }


async def apply_lifecycle(
    settings: Settings,
    *,
    days: int,
    prefix: str,
    apply: bool,
    client: Any | None = None,
) -> dict[str, Any]:
    """返回策略；apply=True 时写 COS 桶配置（client 可注入便于测试）。"""

    policy = build_lifecycle_policy(days, prefix)
    if not apply:
        return policy
    if settings.artifact_store != "cos":
        raise RuntimeError("只有 EVAL__ARTIFACT_STORE=cos 才能应用桶生命周期")
    bucket = settings.artifact_cos_bucket.strip()
    if not bucket:
        raise RuntimeError("缺少 EVAL__ARTIFACT_COS_BUCKET")
    target = client or build_cos_client(settings)
    await asyncio.to_thread(
        target.put_bucket_lifecycle,
        Bucket=bucket,
        LifecycleConfiguration=policy,
    )
    return policy
