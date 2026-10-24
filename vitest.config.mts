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
/**
 * R-31b 覆盖率阈值 ratchet 的唯一数字来源。
 *
 * - 数值取各覆盖单元实测值向下取整;ratchet 只允许上调。web 的 branches 实测 75.11~75.15,
 *   阈值取 74 留 1pp 余量(CI 抖动),记入 W3 verification 偏差 D5。
 * - 子包 vitest 配置从本文件 import 后挂到自己的 coverage.thresholds,因此任何一次
 *   `vitest run --coverage`(含 `npx nx test <项目> -- --run --coverage`)都会真正强制,
 *   而数字只在这里维护(验收口径:grep -rn thresholds vitest.config.mts)。
 * - 键名 = 子配置 test.name;apps/desktop 拆 renderer(node 环境另跑)两个覆盖单元。
 */
export const thresholds = {
  '@beauty/web': { lines: 84, functions: 78, branches: 74, statements: 82 },
  '@beauty/design': { lines: 91, functions: 98, branches: 86, statements: 88 },
  '@beauty/console-ui': {
    lines: 85,
    functions: 66,
    branches: 75,
    statements: 84,
  },
  '@beauty/site': { lines: 97, functions: 95, branches: 96, statements: 97 },
  '@beauty/desktop (renderer)': {
    lines: 83,
    functions: 80,
    branches: 70,
    statements: 79,
  },
  '@beauty/desktop (node)': {
    lines: 83,
    functions: 79,
    branches: 63,
    statements: 76,
  },
} as const;

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
