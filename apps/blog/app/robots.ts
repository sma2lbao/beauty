import type { MetadataRoute } from 'next';

import { blogConfig } from '@/blog.config';

export const dynamic = 'force-static';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: '*', allow: '/' }],
    sitemap: `${blogConfig.siteUrl}/sitemap.xml`,
  };
}
