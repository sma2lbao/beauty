import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

import { thresholds } from '../../vitest.config.mts';

export default defineConfig(() => ({
  root: import.meta.dirname,
  cacheDir: '../../node_modules/.vite/packages/console-ui',
  plugins: [react()],
  test: {
    name: '@beauty/console-ui',
    watch: false,
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}'],
    reporters: ['default'],
    coverage: {
      reportsDirectory: './test-output/vitest/coverage',
      provider: 'v8' as const,
      thresholds: thresholds['@beauty/console-ui'],
    },
  },
}));
