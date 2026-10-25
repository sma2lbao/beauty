# syntax=docker/dockerfile:1
# beauty-moon Web 镜像:构建 apps/web 静态产物,交给 Nginx 托管并同源反代 /api → BFF。
# 约定见 deploy/web/nginx.conf 与 docs/local-dev.md(裸机路径);容器内 upstream 用服务名 bff:8007。
#
# 构建(在仓库根执行):
#   docker build -f deploy/docker/web.Dockerfile -t beauty-moon/web .
# 运行(需一个可达的 bff:8007,通常由 compose 提供):
#   docker run --rm -p 8080:80 --network beauty-moon_default beauty-moon/web
#
# 构建期变量(Vite 只暴露 VITE_ 前缀,构建时固化进静态产物,运行时改不了):
#   VITE_AUTH_MODE 默认 session:登录走 BFF /api/auth/*(同源 Cookie)。
#     不能默认 token:token 模式前端要直连 account-service:8001,而本 Nginx 只反代 /api,
#     浏览器会因无路由/CORS 失败;session 模式所有请求同源 /api,compose 开箱即用。
#   VITE_SITE_URL 控制台"官网"入口;compose 未部署 apps/site,默认留本地 3000 仅作占位。
#
# TODO(W4 后续):仓库根还没有 .dockerignore,本地构建上下文会带上 node_modules/.venv/.git;
# CI checkout 是干净工作树,不受影响。建议补根 .dockerignore(内容见 deploy/web/README.md)。

# ── 构建阶段:Node 22 + npm workspaces 安装 + Vite 构建 ───────────────────────
FROM node:22-bookworm-slim AS builder

ARG VITE_AUTH_MODE=session
ARG VITE_SITE_URL=http://localhost:3000
ENV VITE_AUTH_MODE=${VITE_AUTH_MODE} \
    VITE_SITE_URL=${VITE_SITE_URL}

WORKDIR /app

# npm workspaces 的 npm ci 需要全部 workspace 的 package.json(根 package-lock.json 已锁定),
# 这里复制整个 apps/packages/tools,同时拿到 @beauty/design、@beauty/console-ui 源码
# (仓库内走 @beauty/source 条件直接消费 src/,无需先 build 组件库)。
COPY package.json package-lock.json ./
COPY apps ./apps
COPY packages ./packages
COPY tools ./tools
COPY tsconfig.base.json ./
COPY nx.json ./

# 防御性清理:本地 apps/*/.env* 会被 Vite 读入并覆盖构建期 VITE_* 值(且可能带
# 本机联调配置),导致镜像与 CI 构建不一致。容器里只认 build args。
# 仓库根暂无 .dockerignore,CI 干净 checkout 没有这些文件。
RUN find /app/apps /app/packages -name '.env' -delete \
 && find /app/apps /app/packages -name '.env.*' -not -name '.env.example' -delete

RUN npm ci --no-audit --no-fund

# 走 nx(与本地/CI 同一条构建链):apps/web 的 build 依赖 @beauty/design 与 @beauty/console-ui 的
# dist 类型声明(tsconfig references),单独 npm run build 会报 TS6305;nx.json 的
# targetDefaults.build.dependsOn=["^build"] 保证先构建上游库。产物 apps/web/dist。
ENV NX_DAEMON=false
RUN npx nx build @beauty/web

# ── 运行阶段:nginx:alpine + 静态产物 + 反代配置 ──────────────────────────────
FROM nginx:alpine AS runtime

# 覆盖镜像默认 conf.d/default.conf;server_name 通配、监听 80,TLS 由上层 LB 或
# nginx.conf 内注释的 443 server 块启用(见 deploy/web/README.md)。
COPY deploy/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/apps/web/dist /usr/share/nginx/html

EXPOSE 80

# 健康检查打静态首页(不依赖 BFF;编排层需要 BFF 就绪时用 /healthz 或 /readyz)。
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
