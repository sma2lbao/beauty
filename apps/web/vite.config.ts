import { fileURLToPath, URL } from 'node:url';

import { sentryVitePlugin } from '@sentry/vite-plugin';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * Sourcemap 上传只在 CI/发布机显式给了三个变量时启用:
 * `SENTRY_AUTH_TOKEN`(不要提交)、`SENTRY_ORG`、`SENTRY_PROJECT`。
 * 缺任何一个都正常构建,只是不传 sourcemap、不生成 map 文件。
 */
const sentryUpload = Boolean(
  process.env.SENTRY_AUTH_TOKEN &&
    process.env.SENTRY_ORG &&
    process.env.SENTRY_PROJECT,
);

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    ...(sentryUpload
      ? [
          sentryVitePlugin({
            org: process.env.SENTRY_ORG,
            project: process.env.SENTRY_PROJECT,
            authToken: process.env.SENTRY_AUTH_TOKEN,
            telemetry: false,
            release: {
              name:
                process.env.VITE_SENTRY_RELEASE ??
                process.env.SENTRY_RELEASE,
            },
            sourcemaps: {
              assets: './dist/**',
              filesToDeleteAfterUpload: ['./dist/**/*.map'],
            },
          }),
        ]
      : []),
  ],
  build: {
    // hidden:生成 map 给 Sentry 上传,但不在 JS 末尾写 sourceMappingURL(不对外暴露源码)。
    sourcemap: sentryUpload ? 'hidden' : false,
  },
  server: {
    // 开发期把 /api 代理到 agent-orchestrator(:8002):前端同源、免 CORS
    // (服务端也配了白名单,直连同样可用;见 services/agent-orchestrator 的 CORS 配置)
    proxy: {
      // M0-05/06:SPA 的所有 /api 都走 API 网关(session Cookie / Bearer 兜底);
      // API 网关再按路由转发到 orchestrator/account/workspace/knowledge。
      '/api': {
        target: 'http://127.0.0.1:8007',
        changeOrigin: true,
      },
      // 知识库控制台:knowledge-service(:8006);对外仍是同源,去掉前缀
      '/knowledge-api': {
        target: 'http://127.0.0.1:8006',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/knowledge-api/, ''),
      },
      // 账号控制台:account-service(:8001)
      '/account-api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/account-api/, ''),
      },
      // 工作区控制台:workspace-service(:8005)
      '/workspace-api': {
        target: 'http://127.0.0.1:8005',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/workspace-api/, ''),
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
    // @beauty/design 的 exports 里 @beauty/source 指向 src/index.ts:
    // 仓库内应用直接编译组件源码(改组件即时生效,无需先构建);
    // 不开这个条件的工具链自然落到 dist(发布产物)。
    // 默认值是 ['module', 'browser', 'development|production'],这里是替换而非追加,故一并列出。
    conditions: [
      '@beauty/source',
      'module',
      'browser',
      'development|production',
    ],
  },
});
