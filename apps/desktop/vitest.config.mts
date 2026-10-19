import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/** 渲染进程测试:jsdom + Testing Library。 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Vitest 4 的 cacheDir 是 Vite 顶层配置,不属于 test 对象
  cacheDir: '../../node_modules/.vite/apps/desktop/renderer',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src/renderer', import.meta.url)),
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
    },
    conditions: ['@beauty/source', 'module', 'browser', 'development|production'],
  },
  test: {
    name: '@beauty/desktop (renderer)',
    root: import.meta.dirname,
    watch: false,
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/renderer/vitest.setup.ts'],
    include: ['src/renderer/**/*.{test,spec}.{ts,tsx}'],
    reporters: ['default'],
  },
});
