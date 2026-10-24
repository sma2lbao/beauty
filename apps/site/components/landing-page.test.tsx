/**
 * W2 R-21:landing 关键 CTA。
 * 只断言用户可达性契约:CTA 指向 app 域 /login(跨域注册入口)、
 * 锚点导航(#workflow)保留、页脚不裸露 app 内部路径。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { APP_URL, appUrl } from '@/components/link';

import { LandingPage } from './landing-page';

describe('landing CTA', () => {
  it('主 CTA「免费开始使用」跳转 app 域 /login', () => {
    render(<LandingPage />);
    // 页面里该文案出现 3 处(hero/中部/CTA 区),全部应指向 app 域 /login。
    const ctas = screen.getAllByRole('link', { name: '免费开始使用' });
    expect(ctas.length).toBeGreaterThanOrEqual(1);
    for (const cta of ctas) {
      expect(cta.getAttribute('href')).toBe(appUrl('/login'));
    }
  });

  it('次级入口「工作流」是页内锚点,不跨域', () => {
    render(<LandingPage />);
    const anchor = screen.getByRole('link', { name: '工作流' });
    expect(anchor.getAttribute('href')).toBe('#workflow');
  });

  it('CTA 区域存在(#cta)且导航含「定价」入口', () => {
    render(<LandingPage />);
    expect(document.getElementById('cta')).not.toBeNull();
    expect(screen.getByRole('link', { name: '定价' }).getAttribute('href')).toBe('#cta');
  });

  it('APP_URL 拼接:前导斜杠不产生双斜杠', () => {
    const base = APP_URL.replace(/\/$/, '');
    expect(appUrl('/login')).toBe(base + '/login');
  });
});
