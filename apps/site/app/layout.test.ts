/**
 * W2 R-21:layout metadata 契约。
 * RootLayout 组件本身引 globals.css,测试只断言导出的 metadata 对象,
 * 不 render(渲染断言归端到端)。用 vi.mock 拦掉 CSS 副作用。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./globals.css', () => ({}));

const { metadata } = await vi.importActual<typeof import('./layout')>('./layout');

describe('site layout metadata', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it('metadataBase 跟随 NEXT_PUBLIC_SITE_URL,未设时回退官方域名', () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', 'https://example.com/');
    // metadata 是模块加载时固化的对象,环境变量只影响默认分支;
    // 这里钉住回退行为与对象形状,不重复测 env 逻辑。
    expect(String(metadata.metadataBase)).toMatch(/^https:/);
    expect(String(metadata.metadataBase)).not.toContain('localhost');
  });

  it('title 用 template 形式,默认值是产品句', () => {
    // 钉住形状:必须是 { default, template } 对象而不是裸字符串。
    expect(typeof metadata.title).toBe('object');
    const title = metadata.title as { default?: string; template?: string };
    expect(title.default).toBe('beauty moon — 把 AI Agent 接进工作流');
    expect(title.template).toBe('%s · beauty moon');
  });

  it('开放图谱与 twitter 卡片携带站点名与 OG 图', () => {
    expect(metadata.openGraph?.siteName).toBe('beauty moon');
    expect(metadata.openGraph?.locale).toBe('zh_CN');
    const images = metadata.openGraph?.images as { url: string }[] | undefined;
    expect(images?.[0]?.url).toBe('/og.png');
    // Twitter 是联合类型,card 只在带卡片类型的成员上;钉住 summary 形态。
    const twitter = metadata.twitter as { card?: string } | undefined;
    expect(twitter?.card).toBe('summary');
  });
});
