import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { PostToc } from '@/components/post-toc';
import { renderMdx } from '@/lib/blog/mdx';
import { formatDateLong } from '@/lib/blog/format';
import { getAdjacentPosts, getAllPosts, getPostBySlug } from '@/lib/blog/posts';
import { extractToc } from '@/lib/blog/toc';
import { blogConfig } from '@/blog.config';

export const dynamicParams = false;

function includeDrafts() {
  return process.env.NODE_ENV !== 'production';
}

export function generateStaticParams() {
  return getAllPosts({ includeDrafts: includeDrafts() }).map((post) => ({
    slug: post.slug,
  }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = getPostBySlug(slug, { includeDrafts: includeDrafts() });
  if (!post) {
    return {};
  }

  return {
    title: post.frontmatter.title,
    description: post.frontmatter.description,
    alternates: { canonical: `/posts/${slug}` },
    openGraph: {
      type: 'article',
      title: post.frontmatter.title,
      description: post.frontmatter.description,
      url: `/posts/${slug}`,
      publishedTime: post.frontmatter.date,
      modifiedTime: post.frontmatter.updated ?? post.frontmatter.date,
      tags: post.frontmatter.tags,
    },
  };
}

export default async function PostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = getPostBySlug(slug, { includeDrafts: includeDrafts() });

  if (!post) {
    notFound();
  }

  const toc = extractToc(post.content);
  const adjacent = getAdjacentPosts(slug, { includeDrafts: includeDrafts() });
  const content = await renderMdx(post.content);
  const { frontmatter } = post;
  const canonical = `${blogConfig.siteUrl}/posts/${slug}`;

  return (
    <article className="mx-auto w-full max-w-5xl px-5 sm:px-8">
      <header className="border-b border-ha-border py-12 sm:py-16">
        <div className="flex flex-wrap items-center gap-2 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ha-accent-soft">
          {frontmatter.series ? (
            <Link
              href={`/series/${encodeURIComponent(frontmatter.series)}`}
              className="transition-colors hover:text-ha-text"
            >
              {frontmatter.series}
            </Link>
          ) : (
            <span>{frontmatter.lang}</span>
          )}
          {frontmatter.series && frontmatter.seriesOrder ? (
            <span className="text-ha-faint">
              / {String(frontmatter.seriesOrder).padStart(2, '0')}
            </span>
          ) : null}
        </div>

        <h1 className="mt-4 max-w-[820px] font-serif text-[34px] leading-[1.18] tracking-[-0.02em] text-ha-text sm:text-[46px]">
          {frontmatter.title}
        </h1>
        <p className="mt-5 max-w-[680px] text-[14.5px] leading-7 text-ha-muted">
          {frontmatter.description}
        </p>

        <div className="mt-7 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-[10.5px] text-ha-dim">
          <time dateTime={frontmatter.date}>
            {formatDateLong(frontmatter.date)}
          </time>
          <span aria-hidden>·</span>
          <span>{post.readingMinutes} 分钟</span>
          {frontmatter.updated ? (
            <>
              <span aria-hidden>·</span>
              <span>更新于 {formatDateLong(frontmatter.updated)}</span>
            </>
          ) : null}
          <a
            href={canonical}
            className="ml-auto hidden text-ha-faint transition-colors hover:text-ha-text-2 sm:inline"
          >
            永久链接
          </a>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {frontmatter.tags.map((tag) => (
            <Link
              key={tag}
              href={`/tags/${encodeURIComponent(tag)}`}
              className="rounded-full border border-ha-border px-2.5 py-0.5 font-mono text-[10px] text-ha-dim transition-colors hover:border-ha-accent/60 hover:text-ha-text-2"
            >
              #{tag}
            </Link>
          ))}
        </div>
      </header>

      <div className="grid gap-10 py-10 lg:grid-cols-[minmax(0,1fr)_200px] lg:gap-14">
        <div className="min-w-0">
          {frontmatter.demo ? (
            <aside className="mb-8 rounded-lg border border-ha-border bg-ha-panel/50 p-4">
              <p className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-ha-accent-soft">
                Lab / {frontmatter.demo.status}
              </p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-ha-muted">
                {frontmatter.demo.runtime ? (
                  <span>runtime: {frontmatter.demo.runtime}</span>
                ) : null}
                {frontmatter.demo.run ? (
                  <span>
                    run:{' '}
                    <code className="text-ha-text-2">
                      {frontmatter.demo.run}
                    </code>
                  </span>
                ) : null}
                {frontmatter.demo.port ? (
                  <span>port: {frontmatter.demo.port}</span>
                ) : null}
              </div>
              {frontmatter.demo.live ? (
                <a
                  href={frontmatter.demo.live}
                  className="mt-3 inline-block text-[12px] text-ha-accent-soft underline underline-offset-4"
                >
                  打开在线演示 ↗
                </a>
              ) : null}
            </aside>
          ) : null}

          <div className="prose-post">{content}</div>

          <footer className="mt-14 border-t border-ha-border pt-6">
            <p className="text-[12px] leading-6 text-ha-dim">
              笔记会随实践继续修订;发现错误,欢迎在仓库里开 issue。
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              {adjacent.newer ? (
                <Link
                  href={`/posts/${adjacent.newer.slug}`}
                  className="group rounded-lg border border-ha-border p-4 transition-colors hover:border-ha-accent/50"
                >
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ha-faint">
                    ← 更新
                  </span>
                  <span className="mt-2 block font-serif text-[16px] text-ha-text-2 group-hover:text-ha-text">
                    {adjacent.newer.frontmatter.title}
                  </span>
                </Link>
              ) : (
                <span />
              )}
              {adjacent.older ? (
                <Link
                  href={`/posts/${adjacent.older.slug}`}
                  className="group rounded-lg border border-ha-border p-4 text-right transition-colors hover:border-ha-accent/50"
                >
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ha-faint">
                    更早 →
                  </span>
                  <span className="mt-2 block font-serif text-[16px] text-ha-text-2 group-hover:text-ha-text">
                    {adjacent.older.frontmatter.title}
                  </span>
                </Link>
              ) : null}
            </div>
          </footer>
        </div>

        <aside className="order-first lg:order-last">
          <PostToc items={toc} />
        </aside>
      </div>
    </article>
  );
}
