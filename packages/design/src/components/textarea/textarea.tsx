import type { ComponentPropsWithRef } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * Textarea 多行文本框。
 *
 * 语义类名见同目录 textarea.css;样式与 Input 同源(边框/焦点环/校验态),
 * 高度交由使用方通过 rows/style 指定。
 */

export interface TextareaProps
  extends Omit<ComponentPropsWithRef<'textarea'>, 'size'> {
  /** 校验失败态,展示危险色边框 */
  invalid?: boolean;
}

export function Textarea({
  invalid = false,
  className,
  disabled,
  ...rest
}: TextareaProps) {
  return (
    <textarea
      data-slot="textarea"
      data-invalid={invalid || undefined}
      data-disabled={disabled || undefined}
      className={cn('bd-textarea', className)}
      disabled={disabled}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}
