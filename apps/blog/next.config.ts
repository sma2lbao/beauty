import type { NextConfig } from 'next';

/**
 * 博客是纯静态站:Next App Router + output export。
 * Vercel 独立项目(Root Directory = apps/blog),其他静态托管上传 out/ 即可。
 */
const nextConfig: NextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: false,
  // 直接消费 monorepo 内组件库的 TS 源码(@beauty/source 条件),需由 Next 转译
  transpilePackages: ['@beauty/design'],
};

export default nextConfig;
