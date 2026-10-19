import { builtinModules } from 'node:module';
import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';

/**
 * 预加载脚本构建:electron 的 preload 只支持 CommonJS(沙箱下更严格),
 * 因此输出 dist/preload/index.cjs,由主进程显式引用。
 */
export default defineConfig({
  resolve: {
    alias: {
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist/preload',
    emptyOutDir: true,
    target: 'node22',
    minify: false,
    lib: {
      entry: fileURLToPath(new URL('./src/preload/index.ts', import.meta.url)),
      formats: ['cjs'],
      fileName: () => 'index.cjs',
    },
    rollupOptions: {
      external: [
        'electron',
        ...builtinModules,
        ...builtinModules.map((name) => `node:${name}`),
      ],
    },
  },
});
