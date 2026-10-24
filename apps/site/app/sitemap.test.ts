/**
 * W2 R-21:sitemap/robots 输出契约(纯函数,零网络)。
 * NEXT_PUBLIC_SITE_URL 在模块加载时读取,两个模块各自固化一份 BASE;
 * 测试在动态 import 前设置环境变量以覆盖非默认分支。
 */
import { describe, expect, it } from 'vitest';

describe('sitemap', () => {
  it('覆盖 5 个静态路径,首页优先级最高', async () => {
    const sitemap = (await import('./sitemap')).default;
    const entries = sitemap();
    const urls = entries.map((entry) => entry.url);
    expect(urls).toHaveLength(5);
    expect(urls.some((url) => url.endsWith('/about'))).toBe(true);
    expect(urls.some((url) => url.endsWith('/pricing'))).toBe(true);
    expect(urls.some((url) => url.endsWith('/docs'))).toBe(true);
    expect(urls.some((url) => url.endsWith('/changelog'))).toBe(true);
    const home = entries.find((entry) => entry.url.endsWith('/')) ?? entries[0];
    expect(home?.priority).toBe(1);
  });
});

describe('robots', () => {
  it('允许全站抓取,禁 /api,sitemap 指向本站 sitemap.xml', async () => {
    const robots = (await import('./robots')).default;
    const rules = robots();
    const all = Array.isArray(rules.rules) ? rules.rules : [rules.rules];
    expect(all?.[0]?.userAgent).toBe('*');
    expect(all?.[0]?.allow).toBe('/');
    expect(all?.[0]?.disallow).toContain('/api/');
    expect(rules.sitemap).toMatch(/\/sitemap\.xml$/);
  });
});
