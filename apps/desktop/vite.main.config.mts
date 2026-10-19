import { builtinModules } from 'node:module';
import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';

/** 主进程构建:ESM 单文件,Electron 与 Node 内置模块保持外部依赖。 */
export default defineConfig({
  resolve: {
    alias: {
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist/main',
    emptyOutDir: true,
    target: 'node22',
    minify: false,
    lib: {
      entry: fileURLToPath(new URL('./src/main/main.ts', import.meta.url)),
      formats: ['es'],
      fileName: () => 'main.js',
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
