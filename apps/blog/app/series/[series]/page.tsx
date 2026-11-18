import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { PostCard } from '@/components/post-card';
import { getAllSeries } from '@/lib/blog/posts';

export const dynamicParams = false;

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function generateStaticParams() {
  return getAllSeries().map(({ name }) => ({ series: name }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ series: string }>;
}): Promise<Metadata> {
  const name = safeDecode((await params).series);
  return {
    title: name,
    description: `系列「${name}」的学习笔记。`,
    alternates: { canonical: `/series/${encodeURIComponent(name)}` },
  };
}

export default async function SeriesDetailPage({
  params,
}: {
  params: Promise<{ series: string }>;
}) {
  const name = safeDecode((await params).series);
  const item = getAllSeries().find((entry) => entry.name === name);

  if (!item) {
    notFound();
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-14 sm:px-8 sm:py-20">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        series
      </p>
      <h1 className="mt-4 font-serif text-[34px] tracking-[-0.02em] text-ha-text">
        {item.name}
      </h1>
      <p className="mt-3 text-[13.5px] text-ha-muted">
        {item.posts.length} 篇,按学习顺序排列。
      </p>

      <div className="mt-8">
        {item.posts.map((post) => (
          <PostCard key={post.slug} post={post} />
        ))}
      </div>
    </div>
  );
}
