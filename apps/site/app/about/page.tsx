import type { Metadata } from 'next';

import { SiteShell } from '@/components/site-shell';

export const metadata: Metadata = {
  title: '关于',
  description: 'beauty moon 想解决的问题、工作方式与产品边界。',
};

export default function AboutPage() {
  return (
    <SiteShell
      eyebrow="About"
      title="让 Agent 干活,也让每一步可审计"
      description="beauty moon 不是一个聊天框,而是一套把 AI Agent 接进真实工作流的控制台:任务可拆解、执行可见、变更可审、结果可追溯。"
    >
      <div className="grid gap-6 md:grid-cols-3">
        {[
          {
            title: '任务优先,不是对话优先',
            body: '从一句目标开始,自动拆成可执行步骤;复杂任务先给计划,确认后再动写操作。',
          },
          {
            title: '过程可观测',
            body: '工具调用、终端输出、Token 与耗时实时展开;方向不对可以暂停、改指令或接管。',
          },
          {
            title: '变更可评审',
            body: 'Diff、测试报告与执行日志聚合一屏,逐文件确认后才合入,全程留痕。',
          },
        ].map((item) => (
          <section
            key={item.title}
            className="rounded-xl border border-ha-border bg-ha-panel p-6"
          >
            <h2 className="text-[16px] font-semibold text-ha-text">
              {item.title}
            </h2>
            <p className="mt-3 text-[13px] leading-6 text-ha-muted">
              {item.body}
            </p>
          </section>
        ))}
      </div>
    </SiteShell>
  );
}
