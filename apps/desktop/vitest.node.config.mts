import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vitest/config';

/** 主进程 / 预加载 / 共享契约测试:纯 node 环境,零网络、零 Electron 依赖。 */
export default defineConfig({
  // Vitest 4 的 cacheDir 是 Vite 顶层配置,不属于 test 对象
  cacheDir: '../../node_modules/.vite/apps/desktop/node',
  resolve: {
    alias: {
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
    },
  },
  test: {
    name: '@beauty/desktop (node)',
    root: import.meta.dirname,
    watch: false,
    globals: true,
    environment: 'node',
    include: ['src/main/**/*.{test,spec}.ts', 'src/shared/**/*.{test,spec}.ts'],
    reporters: ['default'],
  },
});
