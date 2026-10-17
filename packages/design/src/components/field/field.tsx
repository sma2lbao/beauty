import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

export interface FieldProps
  extends Omit<ComponentPropsWithoutRef<'div'>, 'children'> {
  /** 字段名;建议同时传 htmlFor 指向控件 id */
  label?: ReactNode;
  /** 关联控件的 id(原生 label 语义) */
  htmlFor?: string;
  /** 辅助说明,不展示错误时出现 */
  hint?: ReactNode;
  /** 校验错误;有值时整体进入 invalid 态并展示错误文案 */
  error?: ReactNode;
  /** 是否必填(仅展示星号标记) */
  required?: boolean;
  /** 控件本体(Input / Textarea / Select 等) */
  children?: ReactNode;
}

/**
 * Field 字段容器:label + 控件 + 提示/错误 的固定结构。
 *
 * 语义类名见同目录 field.css。只负责排版与状态表达,
 * 不改写子控件的 props——需要 `aria-describedby` 时由使用方自行绑定。
 */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  required = false,
  className,
  children,
  ...rest
}: FieldProps) {
  const hasError = error != null && error !== '';

  return (
    <div
      data-slot="field"
      data-invalid={hasError || undefined}
      className={cn('bd-field', className)}
      {...rest}
    >
      {label != null ? (
        <label
          data-slot="field-label"
          className="bd-field__label"
          htmlFor={htmlFor}
        >
          {label}
          {required ? (
            <span className="bd-field__required" aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
      ) : null}
      <div data-slot="field-control" className="bd-field__control">
        {children}
      </div>
      {hasError ? (
        <p data-slot="field-error" className="bd-field__error" role="alert">
          {error}
        </p>
      ) : hint != null ? (
        <p data-slot="field-hint" className="bd-field__hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
