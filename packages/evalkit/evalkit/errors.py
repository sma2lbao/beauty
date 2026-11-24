"""评测平台异常:消息面向人,CLI 直接打印。"""

from __future__ import annotations


class EvalkitError(Exception):
    """评测平台所有可预期错误的基类。"""


class SchemaError(EvalkitError):
    """数据集/用例/grader schema 不合法。"""


class ConfigError(EvalkitError):
    """平台配置不合法。"""


class AdapterError(EvalkitError):
    """adapter 加载或实例化失败。"""


class BaselineError(EvalkitError):
    """基线文件缺失或格式不合法。"""


class RunnerError(EvalkitError):
    """执行阶段的可预期错误(没有可跑用例、adapter 返回类型错误等)。"""
