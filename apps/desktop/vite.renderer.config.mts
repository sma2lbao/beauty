import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * 渲染进程(React 界面)构建:
 * 产物 dist/renderer 由 Electron 通过 loadFile 打开,所以 base 必须是相对路径。
 * @beauty/source 条件与 apps/web 一致:仓库内直接消费 @beauty/design 源码。
 */
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src/renderer', import.meta.url)),
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
    },
    conditions: ['@beauty/source', 'module', 'browser', 'development|production'],
  },
  server: {
    port: 5174,
    strictPort: true,
  },
  build: {
    outDir: 'dist/renderer',
    emptyOutDir: true,
    // Electron 44 自带 Chromium,无需为老浏览器降级
    target: 'chrome130',
  },
});
