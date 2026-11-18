import Link from 'next/link';

import { blogConfig } from '@/blog.config';

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-ha-border/80 bg-ha-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-5xl items-center justify-between gap-6 px-5 sm:px-8">
        <Link href="/" className="group flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid size-7 place-items-center rounded-md bg-ha-accent font-serif text-[15px] leading-none text-white shadow-[0_0_24px_rgba(94,106,210,0.35)]"
          >
            月
          </span>
          <span className="font-serif text-[17px] tracking-[-0.01em] text-ha-text">
            学习笔记
          </span>
          <span className="hidden font-mono text-[9px] uppercase tracking-[0.22em] text-ha-faint transition-colors group-hover:text-ha-dim sm:inline">
            notes
          </span>
        </Link>

        <nav
          aria-label="博客导航"
          className="flex items-center gap-5 text-[12.5px] text-ha-muted"
        >
          {blogConfig.nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="transition-colors hover:text-ha-text"
            >
              {item.label}
            </Link>
          ))}
          <a
            href={blogConfig.mainSiteUrl}
            className="hidden items-center gap-1 border-l border-ha-border pl-5 transition-colors hover:text-ha-text sm:inline-flex"
          >
            官网 <span aria-hidden>↗</span>
          </a>
        </nav>
      </div>
    </header>
  );
}
