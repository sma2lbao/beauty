import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AboutPage } from './about';

describe('AboutPage', () => {
  it('讲的是 AI 办公助手,不是护肤品牌(防回归)', () => {
    const { container } = render(<AboutPage />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      '关于美月',
    );
    const text = container.textContent ?? '';
    expect(text).toContain('AI 办公助手');
    for (const oldCopy of ['护肤', '月相', '肌肤', '成分表', 'SKU']) {
      expect(text).not.toContain(oldCopy);
    }
  });

  it('四条主张都在:先看后做 / 全程留痕 / 诚实 / 边界清晰', () => {
    render(<AboutPage />);

    for (const principle of ['先看后做', '全程留痕', '诚实', '边界清晰']) {
      expect(screen.getByText(principle)).toBeInTheDocument();
    }
    // 正文与高亮 span 都会命中,所以用 getAll
    expect(screen.getAllByText(/再确认一次/).length).toBeGreaterThan(0);
  });
});
