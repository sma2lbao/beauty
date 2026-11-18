import type { Metadata } from 'next';
import Link from 'next/link';

import { formatDate } from '@/lib/blog/format';
import { getArchive } from '@/lib/blog/posts';

export const metadata: Metadata = {
  title: '归档',
  description: '按年份浏览全部学习笔记。',
};

export default function ArchivePage() {
  const years = getArchive();

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-14 sm:px-8 sm:py-20">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        index / archive
      </p>
      <h1 className="mt-4 font-serif text-[34px] tracking-[-0.02em] text-ha-text">
        归档
      </h1>
      <p className="mt-3 text-[13.5px] text-ha-muted">
        共 {years.reduce((sum, year) => sum + year.posts.length, 0)} 篇。
      </p>

      <div className="mt-10 space-y-10">
        {years.map((year) => (
          <section key={year.year}>
            <h2 className="border-b border-ha-border pb-3 font-mono text-[11px] tracking-[0.18em] text-ha-dim">
              {year.year}
            </h2>
            <ul className="mt-3">
              {year.posts.map((post) => (
                <li key={post.slug}>
                  <Link
                    href={`/posts/${post.slug}`}
                    className="group flex items-baseline gap-4 border-b border-ha-border/60 py-2.5"
                  >
                    <time
                      dateTime={post.frontmatter.date}
                      className="w-[72px] shrink-0 font-mono text-[10.5px] text-ha-faint"
                    >
                      {formatDate(post.frontmatter.date)}
                    </time>
                    <span className="min-w-0 flex-1 truncate text-[13.5px] text-ha-text-2 transition-colors group-hover:text-ha-text">
                      {post.frontmatter.title}
                    </span>
                    <span className="hidden font-mono text-[10px] text-ha-ghost sm:inline">
                      {post.frontmatter.tags[0]}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
