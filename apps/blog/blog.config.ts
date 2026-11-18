const rawSiteUrl =
  process.env.NEXT_PUBLIC_BLOG_SITE_URL ?? 'https://blog.beautymoon.cn';

/**
 * 站点级配置。内容层(lib/blog)不读这里,避免测试依赖环境变量;
 * 页面与 metadata 从这里取品牌/导航信息。
 */
export const blogConfig = {
  title: 'beauty moon · 学习笔记',
  shortTitle: '学习笔记',
  description:
    'TypeScript、Python 与 Agent 工程的学习记录:读书笔记、实验和可以跑起来的小项目。',
  siteUrl: rawSiteUrl.replace(/\/+$/, ''),
  author: 'beauty moon',
  locale: 'zh-CN',
  nav: [
    { href: '/', label: '笔记' },
    { href: '/tags', label: '标签' },
    { href: '/series', label: '系列' },
    { href: '/archive', label: '归档' },
  ],
  mainSiteUrl: 'https://site.beautymoon.cn',
} as const;
