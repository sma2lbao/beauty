/**
 * 内页外壳(SiteShell)的导航契约:
 * 页头 GitHub 图标、页脚保留站内导航并追加博客/设计文档两个子站。
 * 与 landing-page.test.tsx 同口径,两边外壳都不许其一漏掉。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ECOSYSTEM_LINKS, GITHUB_URL, appUrl } from '@/components/link';

import { SiteShell } from './site-shell';

function renderShell() {
  return render(
    <SiteShell eyebrow="Docs" title="标题" description="描述">
      <span>正文</span>
    </SiteShell>,
  );
}

describe('SiteShell 页头与页脚', () => {
  it('页头 GitHub 图标指向源码仓库并在新窗口打开', () => {
    renderShell();
    const github = screen.getByRole('link', { name: 'GitHub 源码仓库' });
    expect(github.getAttribute('href')).toBe(GITHUB_URL);
    expect(github.getAttribute('target')).toBe('_blank');
    expect(github.getAttribute('rel')).toBe('noreferrer');
  });

  it('页脚保留控制台入口,并追加博客/设计文档子站', () => {
    renderShell();
    expect(
      screen.getByRole('link', { name: '控制台' }).getAttribute('href'),
    ).toBe(appUrl('/login'));

    for (const site of ECOSYSTEM_LINKS) {
      const link = screen.getByRole('link', { name: new RegExp(site.label) });
      expect(link.getAttribute('href')).toBe(site.href);
      expect(link.getAttribute('target')).toBe('_blank');
      expect(link.textContent).toContain(site.domain);
    }
  });
});
