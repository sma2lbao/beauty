import type { MetadataRoute } from 'next';

import { blogConfig } from '@/blog.config';
import { getAllPosts, getAllSeries, getAllTags } from '@/lib/blog/posts';

export const dynamic = 'force-static';

export default function sitemap(): MetadataRoute.Sitemap {
  const base = blogConfig.siteUrl;
  const posts = getAllPosts();
  const tags = getAllTags(posts);
  const series = getAllSeries(posts);

  return [
    {
      url: `${base}/`,
      lastModified: posts[0] ? new Date(posts[0].frontmatter.date) : undefined,
      changeFrequency: 'weekly',
      priority: 1,
    },
    { url: `${base}/tags`, changeFrequency: 'weekly', priority: 0.6 },
    { url: `${base}/series`, changeFrequency: 'weekly', priority: 0.6 },
    { url: `${base}/archive`, changeFrequency: 'weekly', priority: 0.6 },
    ...posts.map((post) => ({
      url: `${base}/posts/${post.slug}`,
      lastModified: new Date(post.frontmatter.updated ?? post.frontmatter.date),
      changeFrequency: 'monthly' as const,
      priority: 0.8,
    })),
    ...tags.map(({ tag }) => ({
      url: `${base}/tags/${encodeURIComponent(tag)}`,
      changeFrequency: 'weekly' as const,
      priority: 0.4,
    })),
    ...series.map(({ name }) => ({
      url: `${base}/series/${encodeURIComponent(name)}`,
      changeFrequency: 'weekly' as const,
      priority: 0.5,
    })),
  ];
}
