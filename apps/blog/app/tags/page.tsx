import type { Metadata } from 'next';
import Link from 'next/link';

import { getAllTags } from '@/lib/blog/posts';

export const metadata: Metadata = {
  title: '标签',
  description: '按主题浏览学习笔记。',
};

export default function TagsPage() {
  const tags = getAllTags();

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-14 sm:px-8 sm:py-20">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        index / tags
      </p>
      <h1 className="mt-4 font-serif text-[34px] tracking-[-0.02em] text-ha-text">
        标签
      </h1>
      <p className="mt-3 text-[13.5px] text-ha-muted">
        共 {tags.length} 个主题。标签是跨系列的横切索引。
      </p>

      <div className="mt-10 grid gap-2 sm:grid-cols-2">
        {tags.map(({ tag, count }) => (
          <Link
            key={tag}
            href={`/tags/${encodeURIComponent(tag)}`}
            className="group flex items-baseline justify-between gap-4 border-b border-ha-border py-3 transition-colors hover:border-ha-accent/50"
          >
            <span className="text-[14px] text-ha-text-2 group-hover:text-ha-text">
              #{tag}
            </span>
            <span className="font-mono text-[10px] text-ha-faint">
              {String(count).padStart(2, '0')}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
