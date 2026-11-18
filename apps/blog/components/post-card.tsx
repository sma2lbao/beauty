import Link from 'next/link';

import { formatDate } from '@/lib/blog/format';
import type { PostMeta } from '@/lib/blog/posts';

const LANG_LABEL: Record<PostMeta['frontmatter']['lang'], string> = {
  ts: 'TypeScript',
  python: 'Python',
  mixed: 'Lab',
};

export function PostCard({ post }: { post: PostMeta }) {
  const { slug, frontmatter, readingMinutes } = post;

  return (
    <article className="group grid gap-2 border-b border-ha-border py-7 sm:grid-cols-[104px_1fr] sm:gap-10">
      <div className="flex items-center gap-2 pt-1 font-mono text-[11px] text-ha-dim sm:flex-col sm:items-start sm:gap-1.5">
        <time dateTime={frontmatter.date}>{formatDate(frontmatter.date)}</time>
        <span aria-hidden className="text-ha-ghost">
          ·
        </span>
        <span>{readingMinutes} min</span>
      </div>

      <div className="min-w-0">
        <p className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-ha-accent-soft">
          {frontmatter.series ?? LANG_LABEL[frontmatter.lang]}
          {frontmatter.series && frontmatter.seriesOrder ? (
            <span className="ml-2 text-ha-faint">
              {String(frontmatter.seriesOrder).padStart(2, '0')}
            </span>
          ) : null}
        </p>

        <h2 className="mt-2 font-serif text-[23px] leading-snug tracking-[-0.01em] text-ha-text">
          <Link
            href={`/posts/${slug}`}
            className="transition-colors group-hover:text-ha-accent-soft"
          >
            {frontmatter.title}
          </Link>
        </h2>

        <p className="mt-2 max-w-[640px] text-[13.5px] leading-6 text-ha-muted">
          {frontmatter.description}
        </p>

        <div className="mt-3 flex flex-wrap gap-2">
          {frontmatter.tags.map((tag) => (
            <Link
              key={tag}
              href={`/tags/${encodeURIComponent(tag)}`}
              className="rounded-full border border-ha-border px-2 py-0.5 font-mono text-[10px] text-ha-dim transition-colors hover:border-ha-accent/60 hover:text-ha-text-2"
            >
              #{tag}
            </Link>
          ))}
        </div>
      </div>
    </article>
  );
}
