import type { Metadata } from 'next';

import { SiteShell } from '@/components/site-shell';

export const metadata: Metadata = {
  title: '更新日志',
  description: 'beauty moon 控制台与官网的近期更新。',
};

const ENTRIES = [
  {
    date: '2026-10-18',
    title: '会话管理、Markdown 与产物预览',
    body: '会话支持搜索/重命名/归档/软删除与分页;消息与产物统一 Markdown 渲染;支持图片/文本产物预览与下载。',
  },
  {
    date: '2026-10-17',
    title: 'SSE 断线续传',
    body: '事件流支持 Last-Event-ID 续传与指数退避自动重连,执行状态在断线时可见。',
  },
  {
    date: '2026-10-15',
    title: '暗色控制台改版',
    body: '按设计稿重做工作台、任务编排、变更与产物、官网落地页四屏。',
  },
];

export default function ChangelogPage() {
  return (
    <SiteShell
      eyebrow="Changelog"
      title="持续把 Agent 做得更可控"
      description="控制台与官网的近期变化。更细的提交记录在仓库 Git 历史中。"
    >
      <ol className="relative space-y-8 border-l border-ha-border pl-6">
        {ENTRIES.map((entry) => (
          <li key={entry.date} className="relative">
            <span className="absolute -left-[27px] top-1.5 h-2 w-2 rounded-full bg-ha-accent" />
            <p className="font-mono text-[11px] text-ha-dim">{entry.date}</p>
            <h2 className="mt-1 text-[16px] font-semibold text-ha-text">
              {entry.title}
            </h2>
            <p className="mt-2 max-w-[720px] text-[13px] leading-6 text-ha-muted">
              {entry.body}
            </p>
          </li>
        ))}
      </ol>
    </SiteShell>
  );
}
