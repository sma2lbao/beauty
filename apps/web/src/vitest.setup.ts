import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// jsdom 不实现 scrollIntoView(对话页自动滚底用),全局补一个空实现
if (!Element.prototype.scrollIntoView) {
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: () => {},
  });
}

afterEach(() => {
  cleanup();
});
