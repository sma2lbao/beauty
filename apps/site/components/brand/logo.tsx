'use client';

/**
 * 品牌标识（R-20 起单一来源在 @beauty/design）。
 *
 * 官方几何内联在组件库里（packages/design/src/components/brand/），官网、控制台、桌面端共用一份；
 * 本文件只做再导出，保持 '@/components/brand/logo' 这个既有导入路径不变。
 *
 * 走 '@beauty/design/brand' 子入口而不是包根：包根会把整套组件库（含 useState 的 Tabs）
 * 带进 Server Component 依赖图，品牌标识本身只用到 useId，标成 client 即可。
 */
export {
  BrandChip,
  BrandLockup,
  BrandLogo,
  BrandMark,
  BrandTile,
  BrandWordmark,
} from '@beauty/design/brand';
