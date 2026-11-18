/**
 * tone(语义色)与类名映射:状态色到颜色的唯一出口。
 *
 * 业务把后端状态映射成 Tone(如「运行中」→ accent、「失败」→ danger),
 * 组件与页面只消费 tone,不写颜色;具体色值来自 --bd-* 令牌,
 * 类名规则见 styles/tone.css。
 *
 * 变体语义:
 * - solid   实底 + 反色文字(强调用,面积小)
 * - soft    同色底 + 同色文字(状态标签)
 * - outline 同色描边 + 同色底 + 同色文字(胶囊/选中卡片)
 * - text    纯文字色
 * - dot     圆点底色
 */

export type Tone =
  'neutral' | 'accent' | 'info' | 'success' | 'warning' | 'danger';

export type ToneVariant = 'solid' | 'soft' | 'outline' | 'text' | 'dot';

export interface ToneClasses {
  /** 实底 + 反色文字 */
  solid: string;
  /** 同色底 + 同色文字 */
  soft: string;
  /** 同色描边 + 同色底 + 同色文字 */
  outline: string;
  /** 纯文字色 */
  text: string;
  /** 圆点底色 */
  dot: string;
}

/** 单个变体的语义类名,如 toneClass('soft', 'danger') → 'bd-tone-soft--danger' */
export function toneClass(
  variant: ToneVariant,
  tone: Tone = 'neutral',
): string {
  return `bd-tone-${variant}--${tone}`;
}

/** 某个 tone 的全部变体类名,一次取齐供组合使用 */
export function toneClasses(tone: Tone = 'neutral'): ToneClasses {
  return {
    solid: toneClass('solid', tone),
    soft: toneClass('soft', tone),
    outline: toneClass('outline', tone),
    text: toneClass('text', tone),
    dot: toneClass('dot', tone),
  };
}
