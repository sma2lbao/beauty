import type { ComponentPropsWithoutRef, MouseEvent } from 'react';

import { cx } from '../../utils/class-name';

export type TagColor =
  | 'neutral'
  | 'primary'
  | 'success'
  | 'danger'
  | 'warning'
  | 'info';

export interface TagProps extends ComponentPropsWithoutRef<'span'> {
  /** 语义颜色 */
  color?: TagColor;
  /** 是否可关闭,展示关闭按钮 */
  closable?: boolean;
  /** 点击关闭按钮时触发 */
  onClose?: (event: MouseEvent<HTMLButtonElement>) => void;
}

export function Tag({
  color = 'neutral',
  closable = false,
  onClose,
  className,
  children,
  ...rest
}: TagProps) {
  return (
    <span
      className={cx(
        'bd-tag',
        color !== 'neutral' && `bd-tag--${color}`,
        className,
      )}
      {...rest}
    >
      {children}
      {closable ? (
        <button
          type="button"
          className="bd-tag__close"
          aria-label="移除"
          onClick={onClose}
        >
          <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
            <path
              d="M1.5 1.5l7 7m0-7l-7 7"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
        </button>
      ) : null}
    </span>
  );
}
