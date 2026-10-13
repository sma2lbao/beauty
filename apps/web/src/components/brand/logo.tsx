import { useId } from 'react';

import { cn } from '@/lib/utils';

/**
 * 品牌标识(官方几何内联,不引外部图片 → 只有一份来源,不会与资源漂移)。
 *
 * 两套官方几何按**尺寸**分工,不是随手缩放:
 * - `BrandChip`(小尺寸,页头/页脚):取自 `favicon.svg` —— 官方为小尺寸做过
 *   简化,云镂空相对月径更大,24~32px 才读得出"月 + 云";自带夜空底,深浅主题
 *   都有对比;
 * - `BrandMark`(大尺寸,404 等装饰位):取自 `logo-mark.svg` 的纯图形,
 *   官方 README 允许单色场景把月面改为当前主题色平涂,故亮色用品牌暖玫瑰
 *   (米色在浅底对比不足)、暗色用官方月光米 `#FFEFC6`。
 */

/** 夜空底 + 月亮(小尺寸用,favicon 几何)。 */
export function BrandChip({ className }: { className?: string }) {
  const uid = useId().replace(/:/g, '');
  const skyId = `bm-sky-${uid}`;
  const maskId = `bm-cut-${uid}`;
  return (
    <svg
      viewBox="0 0 64 64"
      aria-hidden="true"
      className={cn('shrink-0 rounded-[27%]', className)}
    >
      <defs>
        <radialGradient id={skyId} cx="0.5" cy="0.38" r="0.72">
          <stop offset="0" stopColor="#331E6E" />
          <stop offset="1" stopColor="#150E31" />
        </radialGradient>
        <mask id={maskId}>
          <circle cx="32" cy="30" r="18" fill="#FFFFFF" />
          <g fill="#000000">
            <rect x="29" y="46.5" width="17" height="7" rx="2.5" />
            <circle cx="32.5" cy="45.5" r="2.2" />
            <circle cx="38" cy="44" r="4" />
            <circle cx="42.5" cy="45.5" r="2.5" />
          </g>
        </mask>
      </defs>
      <rect width="64" height="64" rx="14" fill={`url(#${skyId})`} />
      <circle cx="32" cy="30" r="18" fill="#FFEFC6" mask={`url(#${maskId})`} />
    </svg>
  );
}

/** 纯图形 mark(大尺寸用,logo-mark 几何:圆月 + 云为负空间镂空)。 */
export function BrandMark({
  className,
  decorative = false,
}: {
  className?: string;
  /** 装饰性使用(旁边已有可见文字/可访问名)时不给可访问名,避免重复播报。 */
  decorative?: boolean;
}) {
  const maskId = `bm-moon-${useId().replace(/:/g, '')}`;
  return (
    <svg
      viewBox="96 96 320 320"
      {...(decorative
        ? { 'aria-hidden': true }
        : { role: 'img', 'aria-label': 'beauty moon' })}
      className={cn('shrink-0', className)}
    >
      <defs>
        <mask id={maskId}>
          <circle cx="256" cy="256" r="160" fill="#FFFFFF" />
          <g fill="#000000">
            <rect x="248" y="396" width="124" height="46" rx="20" />
            <circle cx="268" cy="392" r="20" />
            <circle cx="312" cy="382" r="34" />
            <circle cx="346" cy="392" r="22" />
          </g>
        </mask>
      </defs>
      <circle
        cx="256"
        cy="256"
        r="160"
        fill="var(--logo-moon)"
        mask={`url(#${maskId})`}
      />
    </svg>
  );
}

/** 月相圆盘:字标里 "moon" 的两个 o 用月相代替(满月 / 月牙)。 */
function MoonPhase({ crescent = false }: { crescent?: boolean }) {
  const uid = useId().replace(/:/g, '');
  const maskId = `bm-phase-${uid}`;
  return (
    <svg
      viewBox="0 0 20 20"
      aria-hidden="true"
      className="mx-[0.02em] inline-block h-[0.53em] w-[0.53em]"
    >
      {crescent ? (
        <>
          {/* 月牙用遮罩挖圆画:牙身够厚,小尺寸才不会看成细环 */}
          <mask id={maskId}>
            <circle cx="10" cy="10" r="9" fill="#FFFFFF" />
            <circle cx="14.6" cy="10" r="7.6" fill="#000000" />
          </mask>
          <circle
            cx="10"
            cy="10"
            r="9"
            fill="var(--logo-moon)"
            mask={`url(#${maskId})`}
          />
        </>
      ) : (
        <circle cx="10" cy="10" r="9" fill="var(--logo-moon)" />
      )}
    </svg>
  );
}

/**
 * 字标:小写衬线(复用应用的展示字体)+ 一处刻意的设计动作——
 * 把 `moon` 的两个 **o** 换成月相(满月 + 月牙),字形动机来自这个词本身的含义,
 * 几何来自官方 mark 的月相语言;其余字母保持单一墨色,不加渐变、不做"某词加粗"
 * (那是最容易读成机器生成的套路),boldness 只花在这一处。
 *
 * 可访问性:整个字标按一个图形对象播报(`role="img"` + `aria-label`),
 * 内部字母与月相对辅助技术隐藏;词间仍是空格。
 */
export function BrandWordmark({ className }: { className?: string }) {
  return (
    <span
      role="img"
      aria-label="beauty moon"
      className={cn(
        'whitespace-nowrap font-display leading-none tracking-tight',
        className,
      )}
    >
      <span aria-hidden="true">beauty m</span>
      <MoonPhase />
      <MoonPhase crescent />
      <span aria-hidden="true">n</span>
    </span>
  );
}

/** 图形 + 字标的横版锁定(页头/页脚共用,避免各处自己拼)。 */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <BrandChip className="h-7 w-7" />
      <BrandWordmark className="text-lg" />
    </span>
  );
}
