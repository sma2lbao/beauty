/**
 * 控制台语义色 tone:实现已收敛到 @beauty/design(src/tone.ts)。
 *
 * 这里保留同名导出,web 与 desktop 的 import 路径不变;颜色不再由 Tailwind
 * 类名承载,而是组件库的 bd-tone-* 语义类与 --bd-* 令牌(见 design 的
 * styles/tone.css),因此控制台换了令牌就换了整套状态色。
 */

export { toneClass, toneClasses } from '@beauty/design';
export type { Tone, ToneClasses, ToneVariant } from '@beauty/design';
