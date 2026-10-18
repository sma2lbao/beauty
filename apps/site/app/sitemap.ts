import type { MetadataRoute } from 'next';

export const dynamic = 'force-static';

const BASE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://www.beautymoon.com';

export default function sitemap(): MetadataRoute.Sitemap {
  const paths = ['', '/about', '/pricing', '/docs', '/changelog'];
  return paths.map((path) => ({
    url: BASE.replace(/\/$/, '') + path,
    lastModified: new Date('2026-10-18'),
    changeFrequency: path === '' ? 'weekly' : 'monthly',
    priority: path === '' ? 1 : 0.6,
  }));
}
