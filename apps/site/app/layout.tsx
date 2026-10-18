import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? 'https://www.beautymoon.com',
  ),
  title: {
    default: 'beauty moon — 把 AI Agent 接进工作流',
    template: '%s · beauty moon',
  },
  description:
    'beauty moon 把任务编排、过程观测与变更评审放进同一个 AI Agent 工作台,让 Agent 的每一步看得见、停得下、可审计。',
  keywords: ['AI Agent', '任务编排', '代码评审', '自动化', 'beauty moon'],
  openGraph: {
    type: 'website',
    locale: 'zh_CN',
    url: '/',
    siteName: 'beauty moon',
    title: 'beauty moon — 把 AI Agent 接进工作流',
    description: '任务编排、过程观测、变更评审在同一个工作台完成。',
    images: [{ url: '/og.png', width: 180, height: 180, alt: 'beauty moon' }],
  },
  twitter: {
    card: 'summary',
    title: 'beauty moon — 把 AI Agent 接进工作流',
    description: '任务编排、过程观测、变更评审在同一个工作台完成。',
    images: ['/og.png'],
  },
  icons: {
    icon: [
      { url: '/favicon.svg', type: 'image/svg+xml' },
      { url: '/favicon-32.png' },
    ],
    apple: '/apple-touch-icon-180.png',
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="dark">
      <body>{children}</body>
    </html>
  );
}
