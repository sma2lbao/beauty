import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { PostCard } from '@/components/post-card';
import { getAllTags, getPostsByTag } from '@/lib/blog/posts';

export const dynamicParams = false;

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function generateStaticParams() {
  return getAllTags().map(({ tag }) => ({ tag }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ tag: string }>;
}): Promise<Metadata> {
  const tag = safeDecode((await params).tag);
  return {
    title: `#${tag}`,
    description: `标签 #${tag} 下的学习笔记。`,
    alternates: { canonical: `/tags/${encodeURIComponent(tag)}` },
  };
}

export default async function TagPage({
  params,
}: {
  params: Promise<{ tag: string }>;
}) {
  const tag = safeDecode((await params).tag);
  const posts = getPostsByTag(tag);

  if (posts.length === 0) {
    notFound();
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-14 sm:px-8 sm:py-20">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        tag
      </p>
      <h1 className="mt-4 font-serif text-[34px] tracking-[-0.02em] text-ha-text">
        #{tag}
      </h1>
      <p className="mt-3 text-[13.5px] text-ha-muted">{posts.length} 篇笔记</p>

      <div className="mt-8">
        {posts.map((post) => (
          <PostCard key={post.slug} post={post} />
        ))}
      </div>
    </div>
  );
}
