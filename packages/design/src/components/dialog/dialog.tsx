import { useEffect, useId, useRef } from 'react';
import type { ComponentPropsWithoutRef, MouseEvent, ReactNode } from 'react';

import { cn } from '../../lib/utils.js';

export interface DialogProps
  extends Omit<
    ComponentPropsWithoutRef<'dialog'>,
    'open' | 'onClose' | 'title'
  > {
  /** 是否打开(受控) */
  open: boolean;
  /** 打开态变化时触发;关闭按钮 / ESC / 点遮罩都会走这里 */
  onOpenChange: (open: boolean) => void;
  /** 标题,同时作为对话框的可访问名 */
  title?: ReactNode;
  /** 描述,补充说明用途 */
  description?: ReactNode;
  /** 底部操作区 */
  footer?: ReactNode;
  /** 是否展示右上角关闭按钮 */
  closable?: boolean;
  /** 点击遮罩是否关闭 */
  closeOnBackdrop?: boolean;
  /** 内容区 */
  children?: ReactNode;
}

/**
 * Dialog 对话框:基于原生 <dialog> 的模态框。
 *
 * 焦点陷阱、ESC 关闭、顶层层级(不受父级 overflow/z-index 影响)都由浏览器提供,
 * 不引第三方定位/portal 依赖。组件受控(open + onOpenChange),状态由使用方持有。
 * 语义类名见同目录 dialog.css。
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  footer,
  closable = true,
  closeOnBackdrop = true,
  className,
  children,
  ...rest
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    if (open) {
      // jsdom 等环境没有 showModal,降级为 open 属性(样式仍生效)
      if (typeof element.showModal === 'function') {
        if (!element.open) element.showModal();
      } else {
        element.setAttribute('open', '');
      }
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }

    if (typeof element.close === 'function') {
      if (element.open) element.close();
    } else {
      element.removeAttribute('open');
    }
    return undefined;
  }, [open]);

  const handleBackdropClick = (event: MouseEvent<HTMLDialogElement>) => {
    // 原生 dialog 的点击目标只有点遮罩时才等于 dialog 本身
    if (closeOnBackdrop && event.target === event.currentTarget) {
      onOpenChange(false);
    }
  };

  return (
    <dialog
      ref={ref}
      data-slot="dialog"
      aria-labelledby={title != null ? titleId : undefined}
      aria-describedby={description != null ? descriptionId : undefined}
      className={cn('bd-dialog', className)}
      onClose={() => onOpenChange(false)}
      onClick={handleBackdropClick}
      {...rest}
    >
      <div data-slot="dialog-panel" className="bd-dialog__panel">
        <div data-slot="dialog-header" className="bd-dialog__header">
          <div className="bd-dialog__heading">
            {title != null ? (
              <h2
                data-slot="dialog-title"
                id={titleId}
                className="bd-dialog__title"
              >
                {title}
              </h2>
            ) : null}
            {description != null ? (
              <p
                data-slot="dialog-description"
                id={descriptionId}
                className="bd-dialog__description"
              >
                {description}
              </p>
            ) : null}
          </div>
          {closable ? (
            <button
              type="button"
              data-slot="dialog-close"
              className="bd-dialog__close"
              aria-label="关闭"
              onClick={() => onOpenChange(false)}
            >
              <svg
                viewBox="0 0 12 12"
                width="12"
                height="12"
                aria-hidden="true"
              >
                <path
                  d="M2 2l8 8m0-8l-8 8"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  fill="none"
                />
              </svg>
            </button>
          ) : null}
        </div>
        <div data-slot="dialog-body" className="bd-dialog__body">
          {children}
        </div>
        {footer != null ? (
          <div data-slot="dialog-footer" className="bd-dialog__footer">
            {footer}
          </div>
        ) : null}
      </div>
    </dialog>
  );
}
