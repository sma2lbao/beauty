import { defineConfig } from 'vitest/config';

/**
 * W2 R-21 vitest workspace 入口(收集哪些项目级配置在此声明)。
 *
 * 子项目共享约定(各 vitest.config.mts 必须保持一致,新项目照抄):
 *   watch: false        —— nx.json 已是 watch 模式,子配置关掉防直跑挂住;
 *   globals: true       —— 测试文件不写 import { it } from 'vitest';
 *   reporters: ['default'];
 *   root: import.meta.dirname;
 *   cacheDir: '../../node_modules/.vite/<项目路径>'。
 * 各项目差异(environment/jsdom 或 node、alias、include、setupFiles)留在
 * 各自 vitest.config.mts,不得在此覆盖。
 */
export default defineConfig({
  test: {
    projects: [
      '**/vite.config.{mjs,js,ts,mts}',
      '**/vitest.config.{mjs,js,ts,mts}',
      // apps 下以各自的 vitest.config 为准，跳过其 vite.config，避免同名项目冲突
      '!apps/*/vite.config.{mjs,js,ts,mts}',
      '!vitest.config.{mjs,js,ts,mts}',
      '!vite.config.{mjs,js,ts,mts}',
    ],
  },
});
