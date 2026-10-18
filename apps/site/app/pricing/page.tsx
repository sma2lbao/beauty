import type { Metadata } from 'next';

import { appUrl } from '@/components/link';
import { SiteShell } from '@/components/site-shell';

export const metadata: Metadata = {
  title: '定价',
  description:
    'beauty moon 的免费额度与订阅方案(占位页,最终价格以产品内为准)。',
};

const PLANS = [
  {
    name: 'Free',
    price: '¥0',
    note: '个人试用',
    features: ['每月免费任务额度', '基础任务编排', '社区支持'],
  },
  {
    name: 'Pro',
    price: '¥99/月',
    note: '个人开发者',
    features: [
      '更高任务额度',
      '并发任务',
      '完整变更评审',
      'Bring your own key',
    ],
  },
  {
    name: 'Team',
    price: '¥299/人/月',
    note: '团队协作',
    features: ['团队工作区', '成员与权限', '审计与用量', '优先支持'],
  },
];

export default function PricingPage() {
  return (
    <SiteShell
      eyebrow="Pricing"
      title="先跑起来,再按需要升级"
      description="免费额度覆盖日常试用;需要更多并发、团队协作与自带模型密钥时再升级。以下为占位方案,最终价格以产品内为准。"
    >
      <div className="grid gap-5 md:grid-cols-3">
        {PLANS.map((plan) => (
          <section
            key={plan.name}
            className="flex flex-col rounded-xl border border-ha-border bg-ha-panel p-6"
          >
            <div className="flex items-baseline justify-between">
              <h2 className="text-[15px] font-semibold text-ha-text">
                {plan.name}
              </h2>
              <span className="text-[12px] text-ha-dim">{plan.note}</span>
            </div>
            <p className="mt-4 text-[30px] font-semibold tracking-[-0.02em] text-ha-text">
              {plan.price}
            </p>
            <ul className="mt-5 flex-1 space-y-2.5">
              {plan.features.map((feature) => (
                <li
                  key={feature}
                  className="flex items-center gap-2 text-[13px] text-ha-text-2"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-ha-accent" />
                  {feature}
                </li>
              ))}
            </ul>
            <a
              href={appUrl('/login')}
              className="mt-6 inline-flex h-9 items-center justify-center rounded-md border border-ha-border-strong bg-ha-panel-2 text-[13px] font-medium text-ha-text-2 transition-colors hover:text-ha-text"
            >
              开始使用
            </a>
          </section>
        ))}
      </div>
    </SiteShell>
  );
}
