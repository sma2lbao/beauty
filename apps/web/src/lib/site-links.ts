/**
 * 官网地址(唯一来源):控制台里的「官网/产品官网」入口都指向 apps/site。
 *
 * 本地 dev 默认 Next.js :3000;生产通过 VITE_SITE_URL 指向 www 域名。
 */
export const SITE_URL = (
  import.meta.env.VITE_SITE_URL ??
  (import.meta.env.PROD
    ? 'https://www.beautymoon.com'
    : 'http://localhost:3000')
).replace(/\/+$/, '');

export function siteUrl(path = '/'): string {
  return SITE_URL + (path.startsWith('/') ? path : '/' + path);
}
