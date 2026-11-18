import Link from 'next/link';

import { PostCard } from '@/components/post-card';
import { getAllPosts, getAllSeries, getAllTags } from '@/lib/blog/posts';

export default function HomePage() {
  const posts = getAllPosts({
    includeDrafts: process.env.NODE_ENV !== 'production',
  });
  const tags = getAllTags(posts).slice(0, 8);
  const series = getAllSeries(posts);

  return (
    <div className="mx-auto w-full max-w-5xl px-5 sm:px-8">
      <section className="border-b border-ha-border py-16 sm:py-24">
        <p className="font-mono text-[9.5px] uppercase tracking-[0.24em] text-ha-accent-soft">
          learning in public / ts + python
        </p>
        <h1 className="mt-6 max-w-[760px] font-serif text-[40px] leading-[1.12] tracking-[-0.025em] text-ha-text sm:text-[62px]">
          把学过的,
          <br />
          <span className="text-ha-muted">写成能跑的东西。</span>
        </h1>
        <p className="mt-6 max-w-[540px] text-[14.5px] leading-7 text-ha-muted">
          这里放我的学习记录:TypeScript 的类型体操、Python 的异步与数据、Agent
          工程里的取舍,以及一些一次性的实验项目。 笔记尽量短,代码尽量能跑。
        </p>
        <div className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-3 font-mono text-[10.5px] uppercase tracking-[0.14em]">
          <a
            href="#notes"
            className="text-ha-text-2 transition-colors hover:text-ha-accent-soft"
          >
            ↓ 最新笔记
          </a>
          <Link
            href="/series"
            className="text-ha-dim transition-colors hover:text-ha-text-2"
          >
            {series.length} 个系列
          </Link>
          <Link
            href="/archive"
            className="text-ha-dim transition-colors hover:text-ha-text-2"
          >
            归档
          </Link>
        </div>
      </section>

      <section id="notes" className="py-12">
        <header className="flex items-baseline justify-between gap-4">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ha-dim">
            最新笔记 / {String(posts.length).padStart(2, '0')}
          </h2>
          <Link
            href="/archive"
            className="text-[12px] text-ha-muted transition-colors hover:text-ha-text"
          >
            全部 →
          </Link>
        </header>

        {posts.length === 0 ? (
          <p className="py-16 text-center text-[13px] text-ha-dim">
            还没有发布笔记。
          </p>
        ) : (
          <div className="mt-4">
            {posts.map((post) => (
              <PostCard key={post.slug} post={post} />
            ))}
          </div>
        )}
      </section>

      {series.length > 0 ? (
        <section className="border-t border-ha-border py-12">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ha-dim">
            系列
          </h2>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            {series.map((item) => (
              <Link
                key={item.name}
                href={`/series/${encodeURIComponent(item.name)}`}
                className="group rounded-lg border border-ha-border bg-ha-panel/40 p-4 transition-colors hover:border-ha-accent/50 hover:bg-ha-panel"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-serif text-[17px] text-ha-text">
                    {item.name}
                  </span>
                  <span className="font-mono text-[10px] text-ha-faint">
                    {item.posts.length} 篇
                  </span>
                </div>
                <p className="mt-2 line-clamp-1 text-[12px] text-ha-dim">
                  {item.posts[item.posts.length - 1]?.frontmatter.description}
                </p>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {tags.length > 0 ? (
        <section className="border-t border-ha-border py-12">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ha-dim">
            标签
          </h2>
          <div className="mt-5 flex flex-wrap gap-2">
            {tags.map(({ tag, count }) => (
              <Link
                key={tag}
                href={`/tags/${encodeURIComponent(tag)}`}
                className="rounded-full border border-ha-border px-3 py-1 font-mono text-[11px] text-ha-muted transition-colors hover:border-ha-accent/60 hover:text-ha-text-2"
              >
                #{tag} <span className="text-ha-faint">{count}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
