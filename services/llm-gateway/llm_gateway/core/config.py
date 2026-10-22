"""服务配置(env 前缀 LLM_GATEWAY__);提供商密钥由 litellm 直接读环境变量。"""

from common.config import BaseAppSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseAppSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_GATEWAY__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "llm-gateway"
    default_model: str = "deepseek/deepseek-chat"  # litellm 路由格式 provider/model(§9)
    # 模型列表(M0-11 静态配置):留空时只返回 default_model
    available_models: list[str] = []
    request_timeout_seconds: float = 60.0
    # 提供商凭据:显式传给 litellm(不依赖 os.environ 注入)
    api_key: str = ""
    api_base: str = ""
    # 联调开关:开启后把 messages 与响应正文(各截断 2000 字)按 debug 落
    # `llm_content`。默认关——prompt 可能含用户数据与工具结果,只应本地临时开。
    log_content: bool = False
    # 嵌入路由(§9):知识库等服务经此向量化,不直连模型提供商
    default_embedding_model: str = "bge-m3"
