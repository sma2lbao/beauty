import type { Metadata } from 'next';
import Link from 'next/link';

import { getAllSeries } from '@/lib/blog/posts';

export const metadata: Metadata = {
  title: '系列',
  description: '按学习路径组织的笔记系列。',
};

export default function SeriesPage() {
  const series = getAllSeries();

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-14 sm:px-8 sm:py-20">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        index / series
      </p>
      <h1 className="mt-4 font-serif text-[34px] tracking-[-0.02em] text-ha-text">
        系列
      </h1>
      <p className="mt-3 text-[13.5px] text-ha-muted">
        系列内按 `seriesOrder` 排序,适合从头读到尾。
      </p>

      <div className="mt-10 space-y-4">
        {series.map((item) => (
          <Link
            key={item.name}
            href={`/series/${encodeURIComponent(item.name)}`}
            className="group block rounded-lg border border-ha-border bg-ha-panel/30 p-5 transition-colors hover:border-ha-accent/50 hover:bg-ha-panel/60"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-serif text-[21px] text-ha-text">
                {item.name}
              </h2>
              <span className="font-mono text-[10px] text-ha-faint">
                {item.posts.length} 篇
              </span>
            </div>
            <ol className="mt-3 space-y-1">
              {item.posts.slice(0, 4).map((post, index) => (
                <li
                  key={post.slug}
                  className="flex items-baseline gap-3 text-[12.5px] text-ha-muted"
                >
                  <span className="font-mono text-[10px] text-ha-faint">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="line-clamp-1">{post.frontmatter.title}</span>
                </li>
              ))}
            </ol>
          </Link>
        ))}
      </div>
    </div>
  );
}
