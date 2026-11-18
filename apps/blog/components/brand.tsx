'use client';

/**
 * 博客品牌标识只从 @beauty/design 取。
 *
 * 品牌组件内部用 useId 生成 SVG 遮罩 id，因此必须经过 client 边界；
 * 这里集中再导出，页头/页脚按需引用，避免每个使用点各自加 'use client'。
 */
export { BrandTile } from '@beauty/design/brand';
