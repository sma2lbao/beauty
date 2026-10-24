import type { NextConfig } from 'next';

/**
 * 官网是纯静态站:Next App Router + output export。
 * 部署到 Vercel 时直接识别;其他静态托管把 out/ 目录上传即可。
 */
const nextConfig: NextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: false,
  // 直接消费 monorepo 内组件库的 TS 源码（@beauty/source 条件），需由 Next 转译
  transpilePackages: ['@beauty/design', '@beauty/console-ui'],
};

export default nextConfig;
