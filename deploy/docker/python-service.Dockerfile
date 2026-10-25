# syntax=docker/dockerfile:1
# beauty-moon Python 服务统一镜像(deploy/docker-compose.yml 复用同一镜像跑 7 个服务)。
#
# 为什么一个镜像:Python 侧是单一 uv workspace——仓库根只有一份 uv.lock 和一份 .venv,
# 根 pyproject.toml 又依赖全部 7 个服务。因此 builder 里从仓库根执行一次
# "uv sync --frozen --no-dev" 就装齐所有服务的运行依赖,运行时靠 SERVICE 选择入口。
# 这正是 RFC-005 §R-36 的约定(docs/refactoring/rfc/005-w4.md)。
#
# 构建(在仓库根执行;context 必须是仓库根,uv.lock 与源码都在那里):
#   docker build -f deploy/docker/python-service.Dockerfile --build-arg SERVICE=bff -t beauty-moon/bff .
# 直接运行(不经 compose;所有配置走 <服务前缀>__* 环境变量):
#   docker run --rm -p 8007:8007 --env-file services/bff/.env beauty-moon/bff
#
# 不进镜像的:node_modules、JS 侧源码、.venv、.git、文档——本文件只 COPY Python workspace。
# TODO(W4 后续):仓库根还没有 .dockerignore,本地构建时上下文仍会带上 node_modules/.git;
# CI checkout 是干净工作树所以不受影响(建议后续补根 .dockerignore,见 deploy/web/README.md)。

# ── 构建阶段:按 uv.lock 精确安装运行依赖(不含 pytest/ruff 等 dev 依赖) ──────────
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

# 显式复制 Python workspace 的清单与源码,避免把 JS 侧大目录带进镜像层。
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services ./services

# 防御性清理:本地工作树里的服务 .env(可能含真实密钥)与 SQLite/产物目录
# 不应进镜像。仓库根暂无 .dockerignore,CI 干净 checkout 没有这些文件;
# 本地构建靠这一步兜底(见 deploy/web/README.md §10)。
RUN find /app/services /app/packages \( -name '.env' -o -name '*.db' \) -delete \
 && find /app/services -maxdepth 2 -type d \( -name '.artifacts' -o -name '.documents' \) -exec rm -rf {} +

# --frozen:不重新解析依赖,严格按提交的 uv.lock 安装(CI 与生产镜像一致);
# --no-dev:不装根 [dependency-groups].dev(pytest/ruff 等),镜像只保留运行依赖。
# 安装的是 editable workspace 包(源码路径在 /app 下,运行阶段同路径复制,导入不受影响)。
RUN uv sync --frozen --no-dev

# ── 运行阶段:仅 Python 运行时 + 上面装好的 /app/.venv ─────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# SERVICE 决定默认入口模块、端口与 HEALTHCHECK 目标;由 compose build args 注入。
ARG SERVICE=bff
ENV SERVICE=${SERVICE} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:${PATH}

# 非 root 运行(uid/gid 10001,与 RFC 约定一致)。
# /data 预建并授权:具名卷首次挂载会继承镜像内属主,account 签名密钥与 workspace
# 交付物因此对 uid 10001 可写。
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/keys /data/artifacts \
 && chown -R 10001:10001 /data

WORKDIR /app
COPY --chown=10001:10001 --from=builder /app /app

USER 10001:10001

# 存活探针:只打本进程 /healthz(不依赖 DB/下游,见 docs/observability.md 探针约定);
# runtime 镜像没有 curl,用标准库 urllib。端口按 SERVICE 映射,与 CMD 同一张表。
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import os, urllib.request; ports = {'account-service': 8001, 'agent-orchestrator': 8002, 'llm-gateway': 8003, 'connector-service': 8004, 'workspace-service': 8005, 'knowledge-service': 8006, 'bff': 8007}; urllib.request.urlopen('http://127.0.0.1:%d/healthz' % ports[os.environ['SERVICE']], timeout=2)"]

# SERVICE -> (uvicorn 模块, 端口)。端口和 docs/local-dev.md、各服务 project.json 的
# serve target 保持一致;--no-access-log 与本地 serve 一致(访问日志由 common 的
# 结构化 http_request 中间件负责,避免重复)。
CMD ["sh", "-c", "case \"$SERVICE\" in account-service) exec uvicorn account.main:app --host 0.0.0.0 --port 8001 --no-access-log ;; agent-orchestrator) exec uvicorn agent_orchestrator.main:app --host 0.0.0.0 --port 8002 --no-access-log ;; llm-gateway) exec uvicorn llm_gateway.main:app --host 0.0.0.0 --port 8003 --no-access-log ;; connector-service) exec uvicorn connector.main:app --host 0.0.0.0 --port 8004 --no-access-log ;; workspace-service) exec uvicorn workspace.main:app --host 0.0.0.0 --port 8005 --no-access-log ;; knowledge-service) exec uvicorn knowledge.main:app --host 0.0.0.0 --port 8006 --no-access-log ;; bff) exec uvicorn bff.main:app --host 0.0.0.0 --port 8007 --no-access-log ;; *) echo \"unknown SERVICE: $SERVICE\" >&2; exit 64 ;; esac"]
