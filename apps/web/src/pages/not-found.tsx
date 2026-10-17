import { Link } from 'react-router';

import { BrandTile } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';

export function NotFoundPage() {
  return (
    <section className="mx-auto flex max-w-2xl flex-col items-center gap-6 px-6 py-24 text-center">
      <BrandTile size={56} className="rounded-xl" />
      <h1 className="text-[28px] font-bold tracking-[-0.01em] text-ha-text">
        这个页面不在清单里
      </h1>
      <p className="max-w-md text-[13px] leading-6 text-ha-muted">
        地址可能拼错了,或者这个页面还没做。
        回工作台继续派活,也可以去任务编排看事件流水。
      </p>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Link
          to="/"
          className={consoleButtonClass({ variant: 'primary', size: 'md' })}
        >
          回到工作台
        </Link>
        <Link
          to="/runs"
          className={consoleButtonClass({ variant: 'secondary', size: 'md' })}
        >
          任务编排
        </Link>
      </div>
    </section>
  );
}
