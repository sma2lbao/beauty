import { describe, expect, it } from 'vitest';

import { titleFromMessage } from './chat';

describe('titleFromMessage', () => {
  it('取首行作为标题', () => {
    expect(titleFromMessage('整理本周邮件成周报\n顺便看看日历')).toBe(
      '整理本周邮件成周报',
    );
  });

  it('空输入给默认标题', () => {
    expect(titleFromMessage('   ')).toBe('新任务');
  });

  it('超长标题截断', () => {
    expect(titleFromMessage('啊'.repeat(50))).toHaveLength(41);
  });
});
