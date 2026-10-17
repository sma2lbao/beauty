import { Link, NavLink } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
import { cn } from '@/lib/utils';

const PUBLIC_NAV = [
  { to: '/landing', label: '产品官网' },
  { to: '/about', label: '关于' },
  { to: '/', label: '控制台' },
];

/** 公开页页头(关于/404 等):与控制台同一套暗色语言,但只保留公开入口。 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-ha-border bg-ha-sidebar/90 backdrop-blur">
      <div className="mx-auto flex h-[64px] max-w-[1120px] items-center justify-between gap-4 px-5 sm:px-8">
        <Link to="/landing" aria-label="beauty moon 官网首页">
          <BrandLockup size={26} textClassName="text-[15px]" />
        </Link>

        <nav aria-label="公开页导航" className="flex items-center gap-1 sm:gap-2">
          {PUBLIC_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
                  isActive
                    ? 'bg-[#1c1d1f] text-ha-text'
                    : 'text-ha-text-3 hover:text-ha-text',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
          <Link
            to="/login"
            className={cn(
              consoleButtonClass({ variant: 'primary', size: 'md' }),
              'ml-1 h-8 px-3',
            )}
          >
            登录
          </Link>
        </nav>
      </div>
    </header>
  );
}
