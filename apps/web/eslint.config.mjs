import { react } from '@beauty/eslint-config';

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
];
