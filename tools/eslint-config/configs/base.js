import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * monorepo 全局忽略:构建产物与缓存目录。
 * Python 项目由 ruff 统一管理,不由 ESLint 覆盖。
 */
export const ignores = [
  '**/dist/**',
  '**/out-tsc/**',
  // Next.js 静态导出与构建产物(apps/site 的 out/ 与 .next/)
  '**/out/**',
  '**/.next/**',
  '**/doc_build/**',
  '**/coverage/**',
  '**/reports/**',
  '**/.cache/**',
  '**/.nx/**',
  // 工具缓存与虚拟环境(uv / uv-python / uv-cache / venv / pytest / ruff 等)
  '**/.uv-cache/**',
  '**/.uv-python/**',
  '**/.venv/**',
  '**/.npm-cache/**',
  '**/.pytest_cache/**',
  '**/.ruff_cache/**',
  '**/.playwright-mcp/**',
];

/**
 * 基础配置:@eslint/js + typescript-eslint recommended,叠加少量仓库约定,
 * 末尾以 eslint-config-prettier 关闭所有与 Prettier 冲突的格式规则
 * (格式统一交给 Prettier,ESLint 只管代码质量)。
 *
 * 适用于纯 TS 包、Node 工具与仓库根部的配置文件。
 */
export function base() {
  return [
    { ignores },
    js.configs.recommended,
    ...tseslint.configs.recommended,
    {
      files: ['**/*.{js,mjs,cjs,jsx,ts,mts,cts,tsx}'],
      languageOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
        globals: { ...globals.browser, ...globals.node },
      },
      rules: {
        'no-console': ['warn', { allow: ['warn', 'error'] }],
        '@typescript-eslint/no-unused-vars': [
          'warn',
          { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
        ],
      },
    },
    {
      // 纯 JS 文件(如本包源码、构建脚本)不适用 TS 编译期规则
      files: ['**/*.{js,mjs,cjs}'],
      rules: {
        '@typescript-eslint/no-require-imports': 'off',
      },
    },
    // W1 R-14 体量上限:单文件行数 warn。
    // 阈值 1400 略高于当前最大文件 chat.tsx(1313 行),首次启用不阻塞现有代码;
    // 后续按 playbook §7.1 逐波收紧(目标 ≤400)。
    {
      files: ['**/*.{js,mjs,cjs,jsx,ts,mts,cts,tsx}'],
      rules: {
        'max-lines': [
          'warn',
          { max: 1400, skipBlankLines: false, skipComments: false },
        ],
      },
    },
    prettier,
  ];
}

/**
 * W1 R-14 前端分层边界(apps/web 目录约定,别名 @/* → apps/web/src/*):
 *   - lib(数据/工具层)不得 import pages 与 components——lib 是最底层;
 *   - components(组件层)不得 import pages——页面在组件之上组装;
 *   - stores(状态层)不得 import pages——不感知路由页面。
 *
 * 实现说明:
 *   - 仓库未安装 eslint-plugin-import 且本波次禁止新增依赖,
 *     用 ESLint 内置 no-restricted-imports 的 patterns 实现;
 *   - 按"来源层"分组:files 钉死谁受约束,patterns 列出不可 import 的目标;
 *     同时封堵 @/ 别名与深层相对路径两种写法,防止绕过别名匹配;
 *   - 范围钉死 apps/web/src/**:packages/design、apps/desktop 结构不同,
 *     规则不适用,避免 no-op 配置噪音;
 *   - 测试文件(*.test.* / *.spec.*)豁免:页面级集成测试需要 render 页面,
 *     属既有模式;生产代码的边界违规才是回流信号;
 *   - 全部 warn 级:现有代码首次命中不阻塞(先全绿,收紧节奏走复盘,RFC-002 §8)。
 */
const WEB_LAYER_BOUNDARIES = [
  {
    layer: 'lib',
    // 同时列仓库根相对(apps/web/...)与 web 项目根相对(src/...)两种形态:
    // nx 从仓库根跑 eslint 时 lint 目标带 apps/web/ 前缀;开发者从 apps/web
    // 直跑 eslint . 时文件路径以 src/ 开头。两种都命中才不会漏检。
    files: [
      'apps/web/src/lib/**/*.ts',
      'apps/web/src/lib/**/*.tsx',
      'src/lib/**/*.ts',
      'src/lib/**/*.tsx',
    ],
    groups: [
      '@/pages',
      '@/components',
      '../../pages',
      '../pages',
      '../../components',
      '../components',
    ],
    message:
      'W1 分层边界: lib 不得 import pages/components(数据与工具层保持最底层)',
  },
  {
    layer: 'components',
    files: [
      'apps/web/src/components/**/*.ts',
      'apps/web/src/components/**/*.tsx',
      'src/components/**/*.ts',
      'src/components/**/*.tsx',
    ],
    groups: ['@/pages', '../../pages', '../pages'],
    message: 'W1 分层边界: components 不得 import pages(页面在组件之上组装)',
  },
  {
    layer: 'stores',
    files: [
      'apps/web/src/stores/**/*.ts',
      'apps/web/src/stores/**/*.tsx',
      'src/stores/**/*.ts',
      'src/stores/**/*.tsx',
    ],
    groups: ['@/pages', '../../pages', '../pages'],
    message: 'W1 分层边界: stores 不得 import pages(状态层不感知路由页面)',
  },
];

/**
 * apps/web 分层边界配置数组(flat config 片段)。
 * 供 react() 组合使用;非 web 项目因 files 不命中而自动 no-op。
 */
export const webLayerBoundary = WEB_LAYER_BOUNDARIES.map(
  ({ files, groups, message }) => ({
    files,
    ignores: ['**/*.test.*', '**/*.spec.*'],
    rules: {
      'no-restricted-imports': [
        'warn',
        { patterns: [{ group: groups, message }] },
      ],
    },
  }),
);
