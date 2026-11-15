import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import { base, webLayerBoundary } from './base.js';

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
    // W1 R-14:apps/web 分层边界(lib/components/stores 不得反向 import pages 等)。
    // 规则片段自带 files 钉死 apps/web/src/**,design/desktop 不命中,自动 no-op。
    ...webLayerBoundary,
    {
      files: ['**/*.{js,jsx,ts,tsx}'],
      plugins: {
        react: reactPlugin,
        'react-hooks': reactHooks,
        'react-refresh': reactRefresh,
      },
      // 显式钉住 React 大版本,不用 'detect'。
      // 原因:eslint-plugin-react 7.37 的 detectReactVersion 会调用
      // context.getFilename(),而 ESLint 10 已移除该方法(升级即崩:
      // "contextOrFilename.getFilename is not a function")。钉住版本可绕开该分支,
      // 其余规则在 10 下实测正常。插件发布 ESLint 10 支持后改回 'detect'。
      settings: { react: { version: '19.3' } },
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
