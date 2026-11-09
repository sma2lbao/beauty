"""llm-gateway:模型网关,全平台唯一的模型调用出口(架构 §5)。"""

import os

# 价目表用 litellm 随包发布的本地副本:启动不发网络请求(离线/受限网络可启动),
# 价格随依赖版本确定,成本口径可复现。需要拉取最新远程价目时显式设 False。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
