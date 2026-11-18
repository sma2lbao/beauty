/**
 * W2 R-21:landing 关键 CTA。
 * 只断言用户可达性契约:CTA 指向 app 域 /login(跨域注册入口)、
 * 锚点导航(#workflow)保留、页脚不裸露 app 内部路径。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  APP_URL,
  ECOSYSTEM_LINKS,
  GITHUB_URL,
  appUrl,
} from '@/components/link';

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

  it('CTA 区域存在(#cta),导航不再暴露「定价」入口', () => {
    render(<LandingPage />);
    expect(document.getElementById('cta')).not.toBeNull();
    // 定价页已下线,首页导航不应再有「定价」项
    expect(screen.queryByRole('link', { name: '定价' })).toBeNull();
  });

  it('页脚不再暴露「公司」区块', () => {
    render(<LandingPage />);
    // 「公司」栏已下线,相关占位链接一并移除
    expect(screen.queryByText('公司')).toBeNull();
    expect(screen.queryByRole('link', { name: '关于我们' })).toBeNull();
  });

  it('APP_URL 拼接:前导斜杠不产生双斜杠', () => {
    const base = APP_URL.replace(/\/$/, '');
    expect(appUrl('/login')).toBe(base + '/login');
  });

  it('页头 GitHub 图标指向源码仓库并在新窗口打开', () => {
    render(<LandingPage />);
    const github = screen.getByRole('link', { name: 'GitHub 源码仓库' });
    expect(github.getAttribute('href')).toBe(GITHUB_URL);
    expect(github.getAttribute('target')).toBe('_blank');
    // target="_blank" 必须配 rel,避免 window.opener 反向控制
    expect(github.getAttribute('rel')).toBe('noreferrer');
  });

  it('页脚列出博客与设计文档子站,并标注域名', () => {
    render(<LandingPage />);
    for (const site of ECOSYSTEM_LINKS) {
      const link = screen.getByRole('link', { name: new RegExp(site.label) });
      expect(link.getAttribute('href')).toBe(site.href);
      expect(link.getAttribute('target')).toBe('_blank');
      // 域名是用户要看到的「相关链接」的一部分,不做隐藏
      expect(link.textContent).toContain(site.domain);
    }
  });
});
