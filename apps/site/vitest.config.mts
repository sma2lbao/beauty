import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * W2 R-21:官网最小测试配置(此前零测试)。
 * 只测纯函数与可直 render 的客户端组件:
 * - sitemap/robots 是纯函数,直接 import 断言返回结构;
 * - layout 的 metadata 是导出对象,断言字段;
 * - landing-page.tsx 标了 'use client',可用 jsdom render,断言关键 CTA href。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('.', import.meta.url)),
    },
  },
  test: {
    name: '@beauty/site',
    root: import.meta.dirname,
    cacheDir: '../../node_modules/.vite/apps/site',
    watch: false,
    globals: true,
    environment: 'jsdom',
    include: ['app/**/*.test.{ts,tsx}', 'components/**/*.test.{ts,tsx}'],
    reporters: ['default'],
  },
});
