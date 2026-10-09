import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
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
