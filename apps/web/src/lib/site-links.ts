/**
 * 官网地址(唯一来源):控制台里的「官网/产品官网」入口都指向 apps/site。
 *
 * 地址解析收敛在 `@/config/env`(R-08):本地 dev 默认 Next.js :3000;
 * 生产通过 VITE_SITE_URL 指向 www 域名。
 */
import { siteUrl as SITE_URL } from '@/config/env';

export { SITE_URL };

export function siteUrl(path = '/'): string {
  return SITE_URL + (path.startsWith('/') ? path : '/' + path);
}
