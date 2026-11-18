import Link from 'next/link';

import { blogConfig } from '@/blog.config';

export function SiteFooter() {
  return (
    <footer className="mt-20 border-t border-ha-border">
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-5 py-8 text-[11.5px] text-ha-dim sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <p>{blogConfig.author} · 学习笔记,不定期更新。</p>
        <nav aria-label="页脚" className="flex flex-wrap items-center gap-5">
          <Link
            href="/archive"
            className="transition-colors hover:text-ha-text"
          >
            归档
          </Link>
          <Link href="/tags" className="transition-colors hover:text-ha-text">
            标签
          </Link>
          <a
            href="/feed.xml"
            className="font-mono text-[10px] uppercase tracking-[0.16em] transition-colors hover:text-ha-text"
          >
            RSS
          </a>
          <a
            href={blogConfig.mainSiteUrl}
            className="transition-colors hover:text-ha-text"
          >
            官网 ↗
          </a>
        </nav>
      </div>
    </footer>
  );
}
