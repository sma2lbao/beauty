import { Moon, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, NavLink } from 'react-router';

import { Button } from '@beauty/design';

import { BrandLogo } from '@/components/brand/logo';
import { cn } from '@/lib/utils';

const navItems = [
  { to: '/', label: '对话', end: true },
  { to: '/runs', label: '控制台' },
  { to: '/knowledge', label: '知识库' },
  { to: '/workspace', label: '工作区' },
  { to: '/outbox', label: '事件队列' },
  { to: '/settings', label: '账号' },
  { to: '/about', label: '关于' },
];

function useDarkMode() {
  const [isDark, setIsDark] = useState(
    () => localStorage.getItem('theme') === 'dark',
  );

  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark);
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
  }, [isDark]);

  return { isDark, toggle: () => setIsDark((value) => !value) };
}

export function SiteHeader() {
  const { isDark, toggle } = useDarkMode();

  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-6">
        <Link to="/" aria-label="beauty moon 首页">
          <BrandLogo />
        </Link>

        <nav className="flex items-center gap-1 sm:gap-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-3 py-1.5 text-sm transition-colors',
                  'focus-visible:outline-2 focus-visible:outline-offset-2',
                  isActive
                    ? 'text-foreground underline decoration-primary underline-offset-8'
                    : 'text-muted-foreground hover:text-foreground',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'}
            className="ml-1"
          >
            {isDark ? <Moon /> : <Sun />}
          </Button>
        </nav>
      </div>
    </header>
  );
}
