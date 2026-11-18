import { base } from '@beauty/eslint-config';

export default [
  ...base(),
  {
    // 一次性案例实验室:自带工具链,不参与主仓 ESLint
    ignores: ['labs/**'],
  },
];
