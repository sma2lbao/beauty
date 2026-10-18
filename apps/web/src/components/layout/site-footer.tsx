import { Link } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { siteUrl } from '@/lib/site-links';

/** 公开页页脚:与控制台/官网一致的暗色信息层级。 */
export function SiteFooter() {
  return (
    <footer className="border-t border-ha-border">
      <div className="mx-auto flex max-w-[1120px] flex-col gap-6 px-5 py-10 sm:flex-row sm:items-start sm:justify-between sm:px-8">
        <div className="flex flex-col gap-2.5">
          <BrandLockup size={24} textClassName="text-[14px]" />
          <p className="max-w-sm text-[12px] leading-5 text-ha-dim">
            把 AI Agent
            真正接进工作流:任务编排、过程观测、变更评审在同一个工作台完成。
          </p>
        </div>
        <div className="flex flex-col gap-2 text-[12px] text-ha-muted sm:items-end">
          <div className="flex gap-4">
            <a
              href={siteUrl('/')}
              className="transition-colors hover:text-ha-text-2"
            >
              产品官网
            </a>
            <Link
              to="/about"
              className="transition-colors hover:text-ha-text-2"
            >
              关于我们
            </Link>
            <Link
              to="/login"
              className="transition-colors hover:text-ha-text-2"
            >
              登录
            </Link>
          </div>
          <p className="font-mono text-ha-faint">
            © 2026 beauty moon · 过程可观测,结果可审计
          </p>
        </div>
      </div>
    </footer>
  );
}
