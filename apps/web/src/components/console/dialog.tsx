/**
 * 控制台对话框(重命名/删除确认等):暗色遮罩 + 居中面板。
 *
 * 不用原生 <dialog>:jsdom 里 showModal/close 支持不完整,测试要 mock;
 * 用 role="dialog" + aria-modal + 手动焦点管理,行为和原生一致但可测:
 * - 打开时聚焦面板内第一个可交互元素,关闭后焦点回到触发元素;
 * - Escape / 点遮罩关闭;Tab 在面板内循环(简单焦点陷阱)。
 */
import { useEffect, useId, useRef, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function ConsoleDialog({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  className,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  className?: string;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const previousFocus = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    previousFocus.current = document.activeElement;
    const panel = panelRef.current;
    const focusTarget =
      panel?.querySelector<HTMLElement>(
        'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])',
      ) ?? panel;
    focusTarget?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel) {
        return;
      }
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusables.length === 0) {
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      const previous = previousFocus.current;
      if (previous instanceof HTMLElement) {
        previous.focus();
      }
    };
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          'relative z-10 w-full max-w-sm rounded-xl border border-ha-border-strong bg-ha-panel p-5 shadow-2xl outline-none',
          className,
        )}
      >
        <h2 id={titleId} className="text-[15px] font-semibold text-ha-text">
          {title}
        </h2>
        {description ? (
          <p
            id={descriptionId}
            className="mt-2 text-[12px] leading-5 text-ha-dim"
          >
            {description}
          </p>
        ) : null}
        {children ? <div className="mt-4">{children}</div> : null}
        {footer ? (
          <div className="mt-5 flex items-center justify-end gap-2">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
