/**
 * 控制台语义色 tone 与类名映射(单一来源)。
 *
 * 设计稿(ardot《Harness Agent Web 控制台》)把状态色限定在小面积标签上:
 * 信息 #2EA7FF、成功 #13DDC4、警告 #FFB454、危险 #FF6B81;
 * 这里把后端状态映射成同一套语义,组件只消费 tone,不再各自写颜色。
 *
 * 本文件不依赖领域类型:RunStatus 等业务状态到 tone 的映射仍留在各应用
 * (如 apps/web 的 lib/presentation/run.ts)。
 */

export type Tone =
  | 'neutral'
  | 'accent'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger';

export type ToneClasses = {
  /** 小面积底(标签/状态点背景) */
  solid: string;
  /** 同色底 + 同色文字 */
  soft: string;
  /** 同色描边(选中卡片/运行中步骤) */
  outline: string;
  /** 纯文字色 */
  text: string;
  /** 圆点 */
  dot: string;
};

export function toneClasses(tone: Tone): ToneClasses {
  switch (tone) {
    case 'accent':
      return {
        solid: 'bg-ha-accent text-white',
        soft: 'bg-ha-accent/15 text-ha-accent-soft',
        outline: 'border-ha-accent/60 bg-ha-accent/10',
        text: 'text-ha-accent-soft',
        dot: 'bg-ha-accent-soft',
      };
    case 'info':
      return {
        solid: 'bg-ha-info text-[#04121c]',
        soft: 'bg-ha-info-bg text-ha-info',
        outline: 'border-ha-info-border bg-ha-info-bg',
        text: 'text-ha-info',
        dot: 'bg-ha-info',
      };
    case 'success':
      return {
        solid: 'bg-ha-success text-[#04140f]',
        soft: 'bg-ha-success-bg text-ha-success',
        outline: 'border-ha-success-border bg-ha-success-bg',
        text: 'text-ha-success',
        dot: 'bg-ha-success',
      };
    case 'warning':
      return {
        solid: 'bg-ha-warning text-[#1a1103]',
        soft: 'bg-ha-warning-bg text-ha-warning',
        outline: 'border-ha-warning-border bg-ha-warning-bg',
        text: 'text-ha-warning',
        dot: 'bg-ha-warning',
      };
    case 'danger':
      return {
        solid: 'bg-ha-danger text-[#1a070c]',
        soft: 'bg-ha-danger-bg text-ha-danger',
        outline: 'border-ha-danger-border bg-ha-danger-bg',
        text: 'text-ha-danger',
        dot: 'bg-ha-danger',
      };
    default:
      return {
        solid: 'bg-ha-panel-2 text-ha-text-2',
        soft: 'bg-ha-panel-2 text-ha-muted',
        outline: 'border-ha-border bg-ha-panel',
        text: 'text-ha-muted',
        dot: 'bg-ha-ghost',
      };
  }
}
