"""服务配置(env 前缀 LLM_GATEWAY__);提供商密钥显式传给 litellm,支持按租户 BYOK。

平台凭据来自本模块(API_KEY/API_BASE,显式入参给 litellm,不依赖
os.environ 注入);租户自带密钥(BYOK)由 ByokResolver 按 account_base_url
向 account-service 解析,命中时代替平台 key。
"""

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
    # 备用模型链(model-routing §5.3):主模型遇到限流/超时/5xx 时按序切换;
    # 留空 = 关闭 fallback(主模型失败直接 502)
    fallback_models: list[str] = []
    request_timeout_seconds: float = 60.0
    # 多模态限制(§5.1):单次请求图片张数与单图 data URL 长度
    max_images_per_request: int = 8
    max_image_data_chars: int = 8_000_000
    # 提供商凭据:显式传给 litellm(不依赖 os.environ 注入)
    api_key: str = ""
    api_base: str = ""
    # 联调开关:开启后把 messages 与响应正文(各截断 2000 字)按 debug 落
    # `llm_content`。默认关——prompt 可能含用户数据与工具结果,只应本地临时开。
    log_content: bool = False
    # 嵌入路由(§9):知识库等服务经此向量化,不直连模型提供商
    default_embedding_model: str = "bge-m3"
    # BYOK(按租户解析提供商密钥,见 README「BYOK」):
    # account-service 内部地址,网关向 GET /v1/byok/{provider}/secret 换密钥
    account_base_url: str = "http://127.0.0.1:8001"
    # 服务间共享密钥(X-Internal-Secret);留空 = BYOK 整体停用,且内部端点不鉴权
    # (仅本地 dev;生产必须配,否则任何内网进程都能调模型)
    internal_secret: str = ""
    byok_enabled: bool = True
    # 正缓存 TTL(秒):租户密钥命中后多久重新解析(密钥轮换的延迟窗口)
    byok_cache_ttl_seconds: float = 300.0
    # 负缓存 TTL(秒):租户没配 key(404)时短缓存,避免每次都打 account
    byok_negative_cache_ttl_seconds: float = 30.0
    # 缓存条目上限(按租户+provider),超出按最近使用淘汰
    byok_cache_max_entries: int = 1024
    # 解析 account 的超时(秒):超时即回退平台 key,不阻塞模型调用
    account_timeout_seconds: float = 5.0
