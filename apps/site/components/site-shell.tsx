import type { ReactNode } from 'react';

import { BrandLockup } from '@/components/brand/logo';
import { appUrl } from '@/components/link';

const NAV = [
  { href: '/', label: '首页' },
  { href: '/about', label: '关于' },
  { href: '/pricing', label: '定价' },
  { href: '/docs', label: '文档' },
  { href: '/changelog', label: '更新日志' },
];

export function SiteShell({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-svh bg-ha-bg text-ha-text-body">
      <header className="border-b border-ha-border">
        <div className="mx-auto flex h-[72px] max-w-[1440px] items-center justify-between gap-6 px-5 sm:px-10 lg:px-16">
          <a href="/" aria-label="beauty moon 官网首页">
            <BrandLockup size={26} textClassName="text-[16px]" />
          </a>
          <nav
            aria-label="官网导航"
            className="hidden items-center gap-8 md:flex"
          >
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="text-[13px] text-ha-text-2 transition-colors hover:text-ha-text"
              >
                {item.label}
              </a>
            ))}
          </nav>
          <a
            href={appUrl('/login')}
            className="inline-flex h-8 items-center rounded-md bg-ha-accent px-3 text-[12px] font-medium text-white transition-colors hover:bg-[#6b77e2]"
          >
            进入控制台
          </a>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1100px] px-5 py-16 sm:px-10 lg:py-24">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ha-accent-soft">
          {eyebrow}
        </p>
        <h1 className="mt-4 max-w-[760px] text-[36px] font-semibold leading-[1.15] tracking-[-0.02em] text-ha-text sm:text-[48px]">
          {title}
        </h1>
        <p className="mt-5 max-w-[680px] text-[15px] leading-7 text-ha-muted">
          {description}
        </p>
        <div className="mt-10">{children}</div>
      </main>

      <footer className="border-t border-ha-border">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-4 px-5 py-8 text-[12px] text-ha-dim sm:flex-row sm:items-center sm:justify-between sm:px-10 lg:px-16">
          <span>beauty moon · 把 AI Agent 接进工作流</span>
          <nav aria-label="页脚" className="flex flex-wrap gap-5">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="transition-colors hover:text-ha-text-2"
              >
                {item.label}
              </a>
            ))}
            <a
              href={appUrl('/login')}
              className="transition-colors hover:text-ha-text-2"
            >
              控制台
            </a>
          </nav>
        </div>
      </footer>
    </div>
  );
}
