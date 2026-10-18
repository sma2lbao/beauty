import type { AnchorHTMLAttributes, ReactNode } from 'react';

/**
 * 官网里的通用链接:
 * - `/landing` 映射到官网首页 `/`;
 * - 控制台路径(/login、/runs、/changes…)映射到 app 域名;
 * - 其余(/about、/pricing…)留在官网内部;
 * - #锚点原样保留。
 */
export const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ??
  (process.env.NODE_ENV === 'production'
    ? 'https://app.beautymoon.com'
    : 'http://localhost:5173');

const APP_PATH_PREFIXES = [
  '/login',
  '/runs',
  '/changes',
  '/knowledge',
  '/settings',
  '/workspace',
  '/outbox',
  '/dashboard',
];

export function appUrl(path: string): string {
  return APP_URL.replace(/\/$/, '') + path;
}

export function Link({
  to,
  children,
  ...rest
}: {
  to: string;
  children: ReactNode;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>) {
  const href =
    to === '/landing'
      ? '/'
      : to.startsWith('#')
        ? to
        : APP_PATH_PREFIXES.some(
              (prefix) => to === prefix || to.startsWith(prefix + '/'),
            )
          ? appUrl(to)
          : to;
  return (
    <a href={href} {...rest}>
      {children}
    </a>
  );
}
