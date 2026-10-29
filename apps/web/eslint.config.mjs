import { react } from '@beauty/eslint-config';

/**
 * apps/web 本地规则补充(W5 R-48)。
 *
 * 共享包 `@beauty/eslint-config` 里的 base()/webLayerBoundary 仍在
 * W1 的宽松档(max-lines 1400、边界只覆盖 lib/components/stores);
 * 本轮结构收敛完成后,在 web 项目内收紧,不动其他包:
 *   - 源文件 ≤400 行、测试文件 ≤500 行(playbook §7.1 的 TS 体量阈值);
 *   - features(业务域)不得反向 import pages(页面只做组装);
 *   - components/console 的通用原语不得 import features(外壳例外,见下)。
 *
 * 全部 warn 级:命中只告警不阻塞,由复盘决定是否升 error。
 */

const MAX_LINES_OPTIONS = {
  skipBlankLines: false,
  skipComments: false,
};

export default [
  ...react(),
  {
    // shadcn/ui 生成组件(src/components/ui)按官方模式同时导出组件与 cva
    // 变体常量,react-refresh 的"文件只导出组件"约束不适用
    files: ['src/components/ui/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // W5 R-48:源文件体量上限 400 行(测试文件另设 500)。
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['**/*.test.*', '**/*.spec.*', '**/*.d.ts'],
    rules: {
      'max-lines': ['warn', { max: 400, ...MAX_LINES_OPTIONS }],
    },
  },
  {
    // W5 R-48:测试文件体量上限 500 行(页面级集成测试天然更长)。
    files: ['src/**/*.test.{ts,tsx}', 'src/**/*.spec.{ts,tsx}'],
    rules: {
      'max-lines': ['warn', { max: 500, ...MAX_LINES_OPTIONS }],
    },
  },
  {
    // W5 R-48:features 层不得 import pages——页面在 features 之上组装。
    // 启用 @/ 别名与深层相对路径两种写法,测试文件豁免(集成测试要 render 页面)。
    files: ['src/features/**/*.{ts,tsx}'],
    ignores: ['**/*.test.*', '**/*.spec.*'],
    rules: {
      'no-restricted-imports': [
        'warn',
        {
          patterns: [
            {
              group: ['@/pages', '../../pages', '../pages'],
              message:
                'W5 分层边界: features 不得 import pages(页面在 features 之上组装)',
            },
          ],
        },
      ],
    },
  },
  {
    // W5 R-48:console 通用原语不得 import features。
    // 外壳(console-layout/sidebar/topbar/status-bar)是组装根,允许 import
    // features,故不在此列。
    files: [
      'src/components/console/ui.tsx',
      'src/components/console/dialog.tsx',
      'src/components/console/markdown.tsx',
      'src/components/console/diff-view.tsx',
      'src/components/console/button-class.ts',
    ],
    rules: {
      'no-restricted-imports': [
        'warn',
        {
          patterns: [
            {
              group: ['@/features', '../features', '../../features'],
              message:
                'W5 分层边界: console 通用原语不得 import features(外壳在 console-layout 等文件里组装)',
            },
          ],
        },
      ],
    },
  },
];
