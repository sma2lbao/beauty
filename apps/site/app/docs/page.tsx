import type { Metadata } from 'next';

import { appUrl } from '@/components/link';
import { SiteShell } from '@/components/site-shell';

export const metadata: Metadata = {
  title: '文档',
  description: 'beauty moon 的快速开始、任务编排、变更评审与 API 文档入口。',
};

const DOCS = [
  {
    title: '快速开始',
    body: '登录控制台,用一句话描述任务,先看计划再放行执行。',
    href: appUrl('/'),
  },
  {
    title: '任务编排',
    body: '看板状态、追加指令、暂停/恢复与重试的完整说明。',
    href: appUrl('/runs'),
  },
  {
    title: '变更与产物',
    body: 'Diff、交付物下载与变更评审的工作方式。',
    href: appUrl('/changes'),
  },
  {
    title: '架构与 API',
    body: '服务架构与接口契约,面向团队集成与开放平台。',
    href: '/about',
  },
];

export default function DocsPage() {
  return (
    <SiteShell
      eyebrow="Docs"
      title="从一句话到一次可评审的变更"
      description="文档站仍在建设中;先通过下面的入口进入控制台或了解产品工作方式。完整的 API 与开放平台文档会随 M1 补齐。"
    >
      <div className="grid gap-5 md:grid-cols-2">
        {DOCS.map((doc) => (
          <a
            key={doc.title}
            href={doc.href}
            className="rounded-xl border border-ha-border bg-ha-panel p-6 transition-colors hover:border-ha-border-strong"
          >
            <h2 className="text-[15px] font-semibold text-ha-text">
              {doc.title}
            </h2>
            <p className="mt-2 text-[13px] leading-6 text-ha-muted">
              {doc.body}
            </p>
            <span className="mt-4 inline-block font-mono text-[11px] text-ha-accent-soft">
              查看 →
            </span>
          </a>
        ))}
      </div>
    </SiteShell>
  );
}
