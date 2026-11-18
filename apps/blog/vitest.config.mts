import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vitest/config';

import { thresholds } from '../../vitest.config.mts';

/**
 * 博客只测内容层(纯 Node,不渲染 React):
 * frontmatter 校验、排序/过滤、标签/系列/归档聚合、TOC 提取。
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('.', import.meta.url)),
    },
  },
  test: {
    name: '@beauty/blog',
    root: import.meta.dirname,
    cacheDir: '../../node_modules/.vite/apps/blog',
    watch: false,
    globals: true,
    environment: 'node',
    include: ['lib/**/*.test.{ts,tsx}'],
    reporters: ['default'],
    coverage: {
      reportsDirectory: './test-output/vitest/coverage',
      provider: 'v8' as const,
      thresholds: thresholds['@beauty/blog'],
    },
  },
});
