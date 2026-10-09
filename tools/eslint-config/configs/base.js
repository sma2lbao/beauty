import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * monorepo 全局忽略:构建产物、缓存目录,以及非 JS 项目(apps/ink、
 * packages/py-utils 是 Python,由 ruff 统一管理)。
 */
export const ignores = [
  '**/dist/**',
  '**/out-tsc/**',
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
  // Python 项目由 ruff 统一管理
  'apps/ink/**',
  'packages/py-utils/**',
];

/**
 * 基础配置:@eslint/js + typescript-eslint recommended,叠加少量仓库约定,
 * 末尾以 eslint-config-prettier 关闭所有与 Prettier 冲突的格式规则
 * (格式统一交给 Prettier,ESLint 只管代码质量)。
 *
 * 适用于纯 TS 包(packages/ts-utils)、Node 工具与仓库根部的配置文件。
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
    prettier,
  ];
}
