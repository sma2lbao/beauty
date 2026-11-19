import type { Metadata } from 'next';

import { SiteShell } from '@/components/site-shell';

export const metadata: Metadata = {
  title: '更新日志',
  description: 'beauty moon 控制台与官网的近期更新。',
};

const ENTRIES = [
  {
    date: '2026-11-18',
    title: '官网接入 GitHub 与子站导航',
    body: '页头新增 GitHub 入口,直达源码仓库;页脚列出学习博客与组件文档,两套外壳共用同一份链接清单。',
  },
  {
    date: '2026-11-18',
    title: '学习博客与组件文档上线',
    body: 'blog.beautymoon.cn 学习博客(MDX 内容管线、RSS、labs 案例围栏)与 design.beautymoon.cn 组件文档站上线;design 新增 Table 与 DropdownMenu,控制台复用它们渲染菜单与表格。',
  },
  {
    date: '2026-11-18',
    title: '控制台读模型事件化',
    body: '会话与运行详情改由读模型事件驱动,运行中不再定时轮询;会话里能看见正在等待的子代理,并可直接跳到子 run。',
  },
  {
    date: '2026-11-17',
    title: '子代理异步执行与恢复',
    body: '子代理改为异步等待,父 run 由子 run 终态唤醒,漏唤醒与超时都有补偿;checkpoint 可续跑,取消在节点边界真打断,回退前先用副作用台账确认。',
  },
  {
    date: '2026-11-16',
    title: '交付物预览、通知与告警',
    body: '沙箱产出的 HTML/图片/PDF 可在控制台直接预览;通知持久化并支持 SSE 回放,新增主动告警;评测行为回归接入 CI。',
  },
  {
    date: '2026-11-15',
    title: '记忆系统与持久终端',
    body: '记忆模块补齐短期压缩、长期记忆、可纠正与时效替代,检索用 BM25 + MMR 并支持重嵌入与 mem0/Zep 适配;沙箱 shell 可在实例间接管,长任务不再随连接断开。',
  },
  {
    date: '2026-11-12',
    title: '部署主线与 E2B 沙箱',
    body: '部署定型为 Vercel 静态层 + 腾讯云轻量(首尔)+ Neon;新增 E2B 沙箱 provider,默认库切本机 compose Postgres,网关路由可观测化,API 域名迁移到 api.beautymoon.cn。',
  },
  {
    date: '2026-11-11',
    title: '多模态输入',
    body: '带图消息不再被误判成任务,网关补齐视觉能力声明,新会话可以直接加附件。',
  },
  {
    date: '2026-11-10',
    title: '工具、后台作业与管理面',
    body: '新增 web_search(tavily/brave)与后台作业 job_start/job_output/job_kill;工具策略引擎三档授权、应用级 webhook、管理面 RBAC 与审计;恢复语义支持超时/优先级与 checkpoint rewind/fork。',
  },
  {
    date: '2026-11-09',
    title: '全链路可观测',
    body: '接入 Langfuse 的 trace/generation span,run 与工具都有 span,运行详情可按 trace_id 深链;采样与属性脱敏,缓存 token 按缓存价计价,Web 端接入 Sentry。',
  },
  {
    date: '2026-11-05',
    title: '账号层与自定义模型',
    body: '多租户产品层拆除,tenant_id 收敛为 account_id;支持 BYOK 自定义提供商与模型清单;工具服务新增沙箱内 run_code。',
  },
  {
    date: '2026-11-04',
    title: '连接器平台',
    body: '连接器支持 OAuth 与静态令牌,含刷新单飞、巡检与生命周期事件,首接 Notion(只读与写工具);对话内写操作就地确认,MCP 预设按工具风险分级。',
  },
  {
    date: '2026-11-03',
    title: '网关更名与域名统一',
    body: 'bff 更名为 api-gateway,域名统一到 .cn(site/app),下线定价页,Vercel 深链回退与 /api 同源代理一并修好。',
  },
  {
    date: '2026-10-29',
    title: '模型路由与重构收口',
    body: '支持按 run 选模型、网关 fallback、结构化输出与多模态输入;W5/W6 重构收口,控制台分层与体量规则落地。',
  },
  {
    date: '2026-10-28',
    title: '子代理委派与事件分类',
    body: 'Agent 可派生子代理并组成 run 树,过程与用量都投影回父 run;事件按 thread/turn/item 分类,计划成为一等条目。',
  },
  {
    date: '2026-10-27',
    title: '会话内执行现场',
    body: '会话里可直接看沙箱文件树、打开文件、连真实终端;write/edit 产出服务端 unified diff,终端与文件树状态持久。',
  },
  {
    date: '2026-10-25',
    title: '0.1.0 正式发布',
    body: '首个正式版本发布,含任务编排、SSE 流式、审批、交付物与知识库;记忆与内置工具服务完成首轮 P0–P3。',
  },
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
          <li key={`${entry.date}-${entry.title}`} className="relative">
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
