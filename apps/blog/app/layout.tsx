import type { Metadata } from 'next';

import { SiteFooter } from '@/components/site-footer';
import { SiteHeader } from '@/components/site-header';
import { blogConfig } from '@/blog.config';

import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL(blogConfig.siteUrl),
  title: {
    default: `${blogConfig.title} — TypeScript、Python 与 Agent 工程`,
    template: `%s · ${blogConfig.shortTitle}`,
  },
  description: blogConfig.description,
  keywords: ['学习笔记', 'TypeScript', 'Python', 'AI Agent', 'beauty moon'],
  authors: [{ name: blogConfig.author }],
  openGraph: {
    type: 'website',
    locale: 'zh_CN',
    url: '/',
    siteName: blogConfig.title,
    title: blogConfig.title,
    description: blogConfig.description,
    images: [
      { url: '/og.svg', width: 1200, height: 630, alt: blogConfig.title },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: blogConfig.title,
    description: blogConfig.description,
    images: ['/og.svg'],
  },
  alternates: {
    types: {
      'application/rss+xml': '/feed.xml',
    },
  },
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="dark">
      <body>
        <a
          href="#content"
          className="sr-only rounded-md bg-ha-accent px-3 py-2 text-sm text-white focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
        >
          跳到正文
        </a>
        <div className="flex min-h-svh flex-col">
          <SiteHeader />
          <main id="content" className="flex-1">
            {children}
          </main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
