import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import { base } from './base.js';

/**
 * React 配置:在 base() 之上叠加 React 19 + React Hooks + react-refresh 规则。
 * 适用于 apps/web、packages/design 等 React 项目(新 JSX transform,
 * 无需 import React)。
 *
 * 插件 flat config 的导出名在版本间有过迁移(eslintrc → flat),
 * 这里做了兼容取值;安装后以实测为准。
 */
export function react() {
  const reactRecommended =
    reactPlugin.configs.flat?.recommended ??
    reactPlugin.configs['flat/recommended'] ??
    reactPlugin.configs.recommended;
  const reactJsxRuntime =
    reactPlugin.configs.flat?.['jsx-runtime'] ??
    reactPlugin.configs['flat/jsx-runtime'] ??
    reactPlugin.configs['jsx-runtime'];
  const hooksRecommended =
    reactHooks.configs['recommended-latest'] ?? reactHooks.configs.recommended;

  return [
    ...base(),
    {
      files: ['**/*.{js,jsx,ts,tsx}'],
      plugins: {
        react: reactPlugin,
        'react-hooks': reactHooks,
        'react-refresh': reactRefresh,
      },
      settings: { react: { version: 'detect' } },
      rules: {
        ...reactRecommended.rules,
        ...reactJsxRuntime.rules,
        ...hooksRecommended.rules,
      },
    },
    {
      files: ['**/*.{jsx,tsx}'],
      rules: {
        'react-refresh/only-export-components': [
          'warn',
          { allowConstantExport: true },
        ],
      },
    },
  ];
}
