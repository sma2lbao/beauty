'use client';

import {
  ArrowRight,
  Check,
  ChevronRight,
  Circle,
  LayoutGrid,
  Columns3,
  GitCompareArrows,
  BookOpen,
  Settings,
  Sparkles,
  Terminal,
} from 'lucide-react';
import { Link } from '@/components/link';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { href: '#capabilities', label: '产品' },
  { href: '#workflow', label: '工作流' },
  { href: '#capabilities', label: '文档' },
  { href: '#cta', label: '定价' },
];

const WORKFLOW_STEPS = [
  {
    number: '01',
    title: '描述任务',
    description:
      '从 Issue、工单或一句话开始。beauty moon 自动拆解为可执行的步骤树,并标出依赖与风险点。',
  },
  {
    number: '02',
    title: 'Agent 沙盒执行',
    description:
      'Agent 在隔离环境里读写代码、跑测试、开分支。每一步都可暂停、可回滚、可随时接管。',
  },
  {
    number: '03',
    title: '审核与合入',
    description:
      'Diff、测试报告与执行日志聚合一屏。确认后一键发起 PR,全程留痕、可审计。',
  },
];

const CAPABILITIES = [
  {
    label: '编排',
    title: '多任务并行,像管团队一样管 Agent',
    description:
      '看板统一管理所有任务的状态流转:谁在跑、卡在哪、等谁审,一眼看清。任务可拆分、可并行、可设置依赖与优先级。',
    link: { to: '/runs', label: '了解任务编排' },
    visual: 'board',
  },
  {
    label: '观测',
    title: '每一步都看得见、停得下',
    description:
      '工具调用、终端输出、Token 消耗与耗时全部实时展开。发现方向不对?随时暂停、改指令或直接接管,不用推倒重来。',
    link: { to: '/changes', label: '了解执行面板' },
    visual: 'steps',
  },
  {
    label: '评审',
    title: '变更可审,合入才放心',
    description:
      'Diff、测试报告、执行日志与评审意见聚合在同一屏。逐文件暂存、逐条回复,确认无误再合入主干。',
    link: { to: '/changes', label: '了解变更评审' },
    visual: 'diff',
  },
] as const;

const FOOTER_COLUMNS = [
  {
    title: '产品',
    links: [
      { label: '任务编排', to: '/runs' },
      { label: '执行面板', to: '/changes' },
      { label: '变更评审', to: '/changes' },
    ],
  },
  {
    title: '资源',
    links: [
      { label: '开发文档', to: '/about' },
      { label: '更新日志', to: '/about' },
      { label: '安全白皮书', to: '/about' },
    ],
  },
  {
    title: '公司',
    links: [
      { label: '关于我们', to: '/about' },
      { label: '加入我们', to: '/about' },
      { label: '联系销售', to: '/about' },
    ],
  },
];

/**
 * 官网落地页(设计稿《04 官网落地页》)。公开页,不套控制台骨架。
 * 文案与配色严格按设计稿;产品截图区域用同语言的小型静态预览拼出来,
 * 不贴一张与真实应用不一致的截图。
 */
export function LandingPage() {
  return (
    <div className="min-h-svh bg-ha-bg text-ha-text-body">
      <header className="border-b border-ha-border">
        <div className="mx-auto flex h-[72px] max-w-[1440px] items-center justify-between gap-6 px-5 sm:px-10 lg:px-16">
          <Link to="/landing" aria-label="beauty moon 官网首页">
            <BrandLockup size={26} textClassName="text-[16px]" />
          </Link>
          <nav
            aria-label="官网导航"
            className="hidden items-center gap-8 md:flex"
          >
            {NAV_ITEMS.map((item) => (
              <a
                key={item.label}
                href={item.href}
                className="text-[13px] text-ha-text-2 transition-colors hover:text-ha-text"
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-4">
            <Link
              to="/login"
              className="text-[13px] font-medium text-ha-text-2 transition-colors hover:text-ha-text"
            >
              登录
            </Link>
            <Link
              to="/login"
              className={cn(
                consoleButtonClass({ variant: 'primary', size: 'md' }),
                'h-[34px] px-4',
              )}
            >
              免费开始使用
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="px-5 pt-16 sm:px-10 sm:pt-22 lg:px-16">
          <div className="mx-auto flex max-w-[960px] flex-col items-center gap-7 text-center">
            <span className="inline-flex h-7 items-center gap-2 rounded-[14px] border border-ha-border-strong bg-ha-panel px-3 text-[12px] text-ha-text-3">
              <span className="h-1.5 w-1.5 rounded-full bg-ha-info" />
              公开内测进行中 · 已支持 GitHub / GitLab
            </span>

            <h1 className="text-[38px] font-bold leading-[1.18] tracking-[-0.02em] text-ha-text sm:text-[48px] lg:text-[56px] lg:leading-[66px]">
              让 AI Agent 真正接手工程交付
            </h1>

            <p className="max-w-[720px] text-[16px] leading-7 text-ha-text-3 sm:text-[18px] sm:leading-[30px]">
              beauty moon
              把任务编排、沙盒执行、过程观测与代码评审放进同一个工作台。 Agent
              在隔离环境里干活,你只需要做关键决策。
            </p>

            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/login"
                className={cn(
                  consoleButtonClass({ variant: 'primary', size: 'lg' }),
                  'h-11 rounded-lg px-[22px] text-[15px] font-semibold',
                )}
              >
                免费开始使用
              </Link>
              <a
                href="#workflow"
                className={cn(
                  consoleButtonClass({ variant: 'secondary', size: 'lg' }),
                  'h-11 rounded-lg px-[22px] text-[15px]',
                )}
              >
                观看 2 分钟演示
              </a>
            </div>

            <p className="font-mono text-[12px] text-ha-faint">
              无需信用卡 · 5 分钟接入现有仓库 · 代码不出私有网络
            </p>
          </div>

          <ProductPreview />
        </section>

        <section
          id="workflow"
          className="px-5 py-20 sm:px-10 sm:py-28 lg:px-16"
        >
          <div className="mx-auto max-w-[1120px]">
            <div className="flex flex-col items-center gap-3.5 text-center">
              <span className="font-mono text-[12px] font-bold text-ha-accent-soft">
                工作流
              </span>
              <h2 className="text-[26px] font-bold tracking-[-0.01em] text-ha-text sm:text-[32px]">
                三步,把一个任务真正交给 Agent
              </h2>
              <p className="max-w-[640px] text-[15px] leading-6 text-ha-text-3">
                不是又一个聊天窗口,而是一条可编排、可观测、可审计的执行链路。
              </p>
            </div>

            <div className="mt-12 grid gap-4 md:grid-cols-3">
              {WORKFLOW_STEPS.map((step) => (
                <div
                  key={step.number}
                  className="flex flex-col gap-3.5 rounded-xl border border-ha-border bg-ha-sidebar p-6 sm:p-7"
                >
                  <span className="font-mono text-[13px] font-bold text-ha-accent-soft">
                    {step.number}
                  </span>
                  <h3 className="text-[17px] font-semibold text-ha-text">
                    {step.title}
                  </h3>
                  <p className="text-[14px] leading-6 text-ha-text-3">
                    {step.description}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section
          id="capabilities"
          className="px-5 pb-20 sm:px-10 sm:pb-28 lg:px-16"
        >
          <div className="mx-auto flex max-w-[1120px] flex-col gap-20 sm:gap-24">
            <div className="flex flex-col items-center gap-3.5 text-center">
              <span className="font-mono text-[12px] font-bold text-ha-accent-soft">
                核心能力
              </span>
              <h2 className="text-[26px] font-bold tracking-[-0.01em] text-ha-text sm:text-[32px]">
                为「信任」而设计的工作台
              </h2>
              <p className="text-[15px] leading-6 text-ha-text-3">
                看得见过程,才敢把任务交出去。
              </p>
            </div>

            {CAPABILITIES.map((capability, index) => (
              <div
                key={capability.label}
                className={cn(
                  'flex flex-col gap-8 lg:flex-row lg:items-center lg:gap-16',
                  index === 1 && 'lg:flex-row-reverse',
                )}
              >
                <div className="flex w-full flex-col gap-4 lg:w-[440px] lg:shrink-0">
                  <span className="font-mono text-[12px] font-bold text-ha-accent-soft">
                    {capability.label}
                  </span>
                  <h3 className="text-[22px] font-bold leading-8 tracking-[-0.01em] text-ha-text sm:text-[26px] sm:leading-9">
                    {capability.title}
                  </h3>
                  <p className="text-[15px] leading-[26px] text-ha-text-3">
                    {capability.description}
                  </p>
                  <Link
                    to={capability.link.to}
                    className="inline-flex w-fit items-center gap-1 text-[14px] font-medium text-ha-accent-soft transition-colors hover:text-ha-text"
                  >
                    {capability.link.label}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>

                <div className="min-w-0 flex-1">
                  <CapabilityVisual kind={capability.visual} />
                </div>
              </div>
            ))}
          </div>
        </section>

        <section
          id="cta"
          className="border-t border-ha-border bg-[radial-gradient(ellipse_at_center,rgba(94,106,210,0.16),transparent_62%)] px-5 py-20 sm:px-10 sm:py-24 lg:px-16"
        >
          <div className="mx-auto flex max-w-[720px] flex-col items-center gap-5 text-center">
            <h2 className="text-[30px] font-bold tracking-[-0.01em] text-ha-text sm:text-[40px]">
              把重复的工程劳动交给 Agent
            </h2>
            <p className="text-[16px] leading-6 text-ha-text-3">
              现在免费开始,5 分钟接入你的第一个仓库。
            </p>
            <Link
              to="/login"
              className={cn(
                consoleButtonClass({ variant: 'primary', size: 'lg' }),
                'mt-2 h-12 rounded-lg px-[26px] text-[15px] font-semibold',
              )}
            >
              免费开始使用
            </Link>
          </div>
        </section>
      </main>

      <footer>
        <div className="mx-auto max-w-[1440px] px-5 sm:px-10 lg:px-16">
          <div className="flex flex-col gap-10 py-12 md:flex-row md:gap-24">
            <div className="flex w-full max-w-[320px] flex-col gap-3">
              <BrandLockup size={22} textClassName="text-[15px]" />
              <p className="text-[13px] leading-[22px] text-ha-dim">
                面向工程团队的 AI Agent
                工作台。编排、执行、观测、评审,一站完成。
              </p>
            </div>
            <div className="grid flex-1 grid-cols-2 gap-8 sm:grid-cols-3">
              {FOOTER_COLUMNS.map((column) => (
                <div key={column.title} className="flex flex-col gap-3">
                  <span className="text-[13px] font-semibold text-ha-text">
                    {column.title}
                  </span>
                  {column.links.map((link) => (
                    <Link
                      key={link.label}
                      to={link.to}
                      className="w-fit text-[13px] text-ha-muted transition-colors hover:text-ha-text-2"
                    >
                      {link.label}
                    </Link>
                  ))}
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 border-t border-ha-border py-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="font-mono text-[12px] text-ha-faint">
              Copyright 2026 beauty moon
            </p>
            <p className="font-mono text-[12px] text-ha-faint">
              服务状态 · 隐私政策 · 沪ICP备2026001234号
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

/* ── 产品预览:用真实的控制台语言拼一个小尺寸静态窗口 ─────────────── */

const PREVIEW_NAV = [
  { icon: LayoutGrid, label: '工作台', active: true },
  { icon: Columns3, label: '任务编排' },
  { icon: GitCompareArrows, label: '变更' },
  { icon: BookOpen, label: '知识库' },
  { icon: Settings, label: '设置' },
];

const PREVIEW_STEPS = [
  {
    title: '检索知识库与关联文档',
    meta: '完成 · 1.8s · 12 篇文档',
    done: true,
  },
  { title: '整理文档要点与待办', meta: '完成 · 2.1s', done: true },
  { title: '生成周报草稿', meta: '完成 · 0.9s', done: true },
  { title: '等待确认对外发布', meta: '运行中 · 3.2s', active: true },
  { title: '归档并交付周报', meta: '排队中', done: false },
];

function ProductPreview() {
  return (
    <div
      aria-hidden="true"
      className="relative mx-auto mt-14 w-full max-w-[1120px] overflow-hidden rounded-2xl border border-white/10 bg-ha-sidebar shadow-[0_40px_120px_rgba(0,0,0,0.65)]"
    >
      <div className="flex h-[420px] sm:h-[520px]">
        <div className="hidden w-[172px] shrink-0 flex-col gap-4 border-r border-ha-border p-3 md:flex">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2">
              <span className="grid h-5 w-5 place-items-center rounded-[5px] bg-ha-accent">
                <Sparkles className="h-3 w-3 text-white" />
              </span>
              <span className="text-[11px] font-semibold text-ha-text">
                beauty moon
              </span>
            </span>
            <span className="rounded-[3px] border border-ha-border-strong bg-ha-panel-2 px-1 font-mono text-[8px] text-ha-muted">
              PROD
            </span>
          </div>
          <div className="flex h-7 items-center justify-center rounded-md bg-ha-accent text-[10px] font-semibold text-white">
            + 新建任务
          </div>
          <div className="flex flex-col gap-0.5">
            {PREVIEW_NAV.map((item) => (
              <span
                key={item.label}
                className={cn(
                  'flex h-6 items-center gap-2 rounded-md px-2 text-[10px]',
                  item.active ? 'bg-[#1c1d1f] text-ha-text' : 'text-ha-text-3',
                )}
              >
                <item.icon className="h-3 w-3" />
                {item.label}
              </span>
            ))}
          </div>
          <div className="mt-1 flex flex-col gap-2">
            <span className="px-1 text-[8px] font-semibold text-ha-dim">
              最近会话
            </span>
            {[
              {
                title: '把本周文档整理成周报',
                meta: '运行中 · 12 分钟前',
                tone: 'bg-ha-info',
              },
              {
                title: '修复 webhook 重试竞态',
                meta: '已完成 · 1 小时前',
                tone: 'bg-ha-success',
              },
              {
                title: '补齐 /v2/orders 契约测试',
                meta: '待审核 · 昨天',
                tone: 'bg-ha-warning',
              },
            ].map((item) => (
              <span
                key={item.title}
                className={cn(
                  'flex items-start gap-1.5 rounded-md px-1.5 py-1.5',
                  item.title === '把本周文档整理成周报' && 'bg-[#1c1d1f]',
                )}
              >
                <span
                  className={cn(
                    'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                    item.tone,
                  )}
                />
                <span className="min-w-0">
                  <span className="block truncate text-[9px] text-ha-text-2">
                    {item.title}
                  </span>
                  <span className="mt-0.5 block font-mono text-[8px] text-ha-dim">
                    {item.meta}
                  </span>
                </span>
              </span>
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-8 shrink-0 items-center justify-between border-b border-ha-border px-3">
            <span className="flex items-center gap-1.5 font-mono text-[8px] text-ha-dim">
              beauty moon
              <ChevronRight className="h-2 w-2 text-ha-ghost" />
              工作台
              <ChevronRight className="h-2 w-2 text-ha-ghost" />
              <span className="text-[9px] font-semibold text-ha-text">
                会话 #A12F
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-5 items-center gap-1 rounded border border-ha-border-strong bg-ha-panel-2 px-1.5 font-mono text-[8px] text-ha-text-2 sm:inline-flex">
                自动路由
              </span>
              <span className="inline-flex h-5 items-center gap-1 rounded border border-ha-info-border bg-ha-info-bg px-1.5 text-[8px] text-ha-info">
                <span className="h-1 w-1 animate-pulse rounded-full bg-ha-info" />
                执行中
              </span>
            </span>
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 sm:p-4">
            <div>
              <span className="font-mono text-[8px] text-ha-faint">
                你 · 12:04
              </span>
              <p className="mt-1 text-[10px] leading-4 text-ha-text-body sm:text-[11px]">
                把本周文档整理成周报,顺带列出需要我确认的待办。
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <span className="flex items-center gap-1.5">
                <span className="grid h-3.5 w-3.5 place-items-center rounded-[4px] bg-[#1f2333]">
                  <Sparkles className="h-2 w-2 text-ha-accent-soft" />
                </span>
                <span className="text-[9px] font-semibold text-ha-text">
                  beauty moon Agent
                </span>
                <span className="font-mono text-[8px] text-ha-dim">
                  已执行 4 步 · 8m 12s
                </span>
              </span>
              <p className="text-[10px] leading-4 text-ha-text-2">
                已读完本周 12
                篇文档,按项目归了三类;周报草稿在生成,对外写入前会等你确认。
              </p>
              <div className="flex items-center gap-2 rounded-md border border-ha-border bg-ha-panel px-2 py-1.5">
                <Terminal className="h-2.5 w-2.5 text-ha-muted" />
                <span className="font-mono text-[8px] font-medium text-ha-text-body">
                  search_knowledge
                </span>
                <span className="truncate font-mono text-[8px] text-ha-dim">
                  top_k=8
                </span>
                <span className="ml-auto font-mono text-[8px] font-medium text-ha-success">
                  完成 · 0.6s
                </span>
              </div>
              <div className="rounded-md border border-ha-info-border bg-ha-info-bg px-2 py-1.5">
                <div className="flex items-center gap-2">
                  <Terminal className="h-2.5 w-2.5 text-ha-info" />
                  <span className="font-mono text-[8px] font-medium text-ha-text-body">
                    draft_weekly_report
                  </span>
                  <span className="truncate font-mono text-[8px] text-ha-dim">
                    第 2 段
                  </span>
                  <span className="ml-auto font-mono text-[8px] font-medium text-ha-info">
                    运行中 · 2.1s
                  </span>
                </div>
                <div className="mt-1.5 h-[2px] overflow-hidden rounded-full bg-[#0b1b26]">
                  <div className="h-full w-1/2 rounded-full bg-ha-info" />
                </div>
              </div>
            </div>

            <div className="mt-auto rounded-md border border-ha-border-strong bg-ha-panel p-2">
              <span className="block text-[9px] text-ha-dim">
                描述下一步任务,或说说你想调整的地方…
              </span>
              <div className="mt-1.5 flex items-center justify-between">
                <span className="font-mono text-[8px] text-ha-faint">
                  自动分流
                </span>
                <span className="grid h-4 w-4 place-items-center rounded bg-ha-accent">
                  <ArrowRight className="h-2.5 w-2.5 -rotate-90 text-white" />
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="hidden w-[190px] shrink-0 flex-col border-l border-ha-border lg:flex">
          <div className="flex h-9 items-center gap-1.5 border-b border-ha-border px-2">
            <span className="rounded bg-[#1c1d1f] px-1.5 py-0.5 text-[9px] font-semibold text-ha-text">
              执行步骤
            </span>
            <span className="px-1 text-[9px] text-ha-muted">变更</span>
            <span className="px-1 text-[9px] text-ha-muted">终端</span>
          </div>
          <div className="flex flex-col gap-1.5 p-2">
            {PREVIEW_STEPS.map((step) => (
              <span
                key={step.title}
                className={cn(
                  'flex items-start gap-1.5 rounded-md border px-1.5 py-1.5',
                  step.active
                    ? 'border-ha-info-border bg-ha-info-bg'
                    : 'border-ha-border bg-ha-panel',
                )}
              >
                {step.done ? (
                  <span className="mt-0.5 grid h-2.5 w-2.5 place-items-center rounded-full border border-ha-success bg-ha-success-bg">
                    <Check className="h-1.5 w-1.5 text-ha-success" />
                  </span>
                ) : step.active ? (
                  <Circle className="mt-0.5 h-2.5 w-2.5 text-ha-info" />
                ) : (
                  <Circle className="mt-0.5 h-2.5 w-2.5 text-ha-ghost" />
                )}
                <span className="min-w-0">
                  <span className="block truncate text-[8px] text-ha-text-body">
                    {step.title}
                  </span>
                  <span
                    className={cn(
                      'mt-0.5 block truncate font-mono text-[7px]',
                      step.active ? 'text-ha-info' : 'text-ha-dim',
                    )}
                  >
                    {step.meta}
                  </span>
                </span>
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-ha-sidebar to-transparent" />
    </div>
  );
}

function CapabilityVisual({ kind }: { kind: 'board' | 'steps' | 'diff' }) {
  if (kind === 'board') {
    return (
      <div className="flex h-[320px] gap-2.5 rounded-xl border border-ha-border bg-ha-sidebar p-5">
        {[
          {
            title: '待启动 · 2',
            tone: 'text-ha-muted',
            cards: [
              {
                title: '接入 Stripe Webhook 重试',
                tone: 'border-ha-border-strong bg-ha-panel-2',
              },
              {
                title: '更新 OpenAPI 契约文档',
                tone: 'border-ha-border-strong bg-ha-panel-2',
              },
            ],
          },
          {
            title: '执行中 · 3',
            tone: 'text-ha-info',
            cards: [
              {
                title: '重构 auth 中间件鉴权链路',
                tone: 'border-ha-info-border bg-ha-info-bg',
              },
              {
                title: '迁移日志到结构化输出',
                tone: 'border-ha-info-border bg-ha-info-bg',
              },
            ],
          },
          {
            title: '待审核 · 2',
            tone: 'text-ha-warning',
            cards: [
              {
                title: '缓存层读写穿透修复',
                tone: 'border-ha-warning-border bg-ha-warning-bg',
              },
            ],
          },
        ].map((column) => (
          <div
            key={column.title}
            className="flex min-w-0 flex-1 flex-col gap-2"
          >
            <span
              className={cn(
                'rounded-md bg-ha-panel px-2.5 py-1.5 font-mono text-[11px] font-medium',
                column.tone,
              )}
            >
              {column.title}
            </span>
            {column.cards.map((card) => (
              <span
                key={card.title}
                className={cn(
                  'rounded-md border px-2.5 py-2.5 text-[11px] leading-4 text-ha-text-2',
                  card.tone,
                )}
              >
                {card.title}
              </span>
            ))}
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'steps') {
    return (
      <div className="flex h-[320px] flex-col gap-2.5 rounded-xl border border-ha-border bg-ha-sidebar p-5">
        {[
          { title: '扫描仓库结构与依赖关系', state: '完成', done: true },
          { title: '写入 session-guard.ts', state: '运行中', active: true },
          { title: '运行测试套件并回归', state: '排队中' },
          { title: '提交 PR 并请求人工评审', state: '排队中' },
        ].map((step) => (
          <div
            key={step.title}
            className={cn(
              'flex items-center gap-2.5 rounded-lg border px-3.5 py-3',
              step.active
                ? 'border-ha-info-border bg-ha-info-bg'
                : 'border-ha-border bg-ha-panel',
            )}
          >
            <span
              className={cn(
                'h-[7px] w-[7px] shrink-0 rounded-full',
                step.done
                  ? 'bg-ha-success'
                  : step.active
                    ? 'animate-pulse bg-ha-info'
                    : 'bg-ha-ghost',
              )}
            />
            <span
              className={cn(
                'min-w-0 flex-1 truncate text-[12px]',
                step.done || step.active
                  ? 'text-ha-text-body'
                  : 'text-ha-muted',
              )}
            >
              {step.title}
            </span>
            <span
              className={cn(
                'shrink-0 font-mono text-[11px] font-medium',
                step.done
                  ? 'text-ha-success'
                  : step.active
                    ? 'text-ha-info'
                    : 'text-ha-faint',
              )}
            >
              {step.state}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex h-[320px] flex-col gap-2 rounded-xl border border-ha-border bg-ha-sidebar p-5 font-mono text-[12px] leading-5">
      <span className="rounded-md bg-ha-panel-3 px-3 py-1 text-[11px] text-ha-faint">
        @@ -42,7 +42,26 @@ session-guard.ts
      </span>
      <span className="rounded-md bg-ha-diff-del-bg px-3 py-1 text-ha-diff-del-text">
        - const session = req.cookies?.sid;
      </span>
      <span className="rounded-md bg-ha-diff-add-bg px-3 py-1 text-ha-diff-add-text">
        + const session = await resolveSession(req);
      </span>
      <span className="rounded-md bg-ha-diff-add-bg px-3 py-1 text-ha-diff-add-text">
        + if (!session) return guard.reject(res, 401);
      </span>
      <span className="px-3 py-1 text-ha-dim"> return next();</span>
      <span className="rounded-md bg-ha-diff-add-bg px-3 py-1 text-ha-diff-add-text">
        + await guard.attach(session);
      </span>
    </div>
  );
}
