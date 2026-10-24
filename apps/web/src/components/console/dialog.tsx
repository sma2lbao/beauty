/**
 * 控制台对话框(重命名/删除确认等)。
 *
 * R-26 起不再自建遮罩/焦点陷阱:面板结构与可访问性(aria-labelledby、ESC、
 * 点遮罩、body 滚动锁、原生 top-layer)都来自 @beauty/design 的 Dialog,
 * 这里只保留控制台历史 API(open/title/description/footer/onClose/className)
 * 并补一件组件库刻意交给宿主的事——关闭后把焦点还给触发元素。
 *
 * 关闭态返回 null:调用点(工作区管理弹窗里的行内菜单)依赖「未打开时不在 DOM 里」,
 * 也避免未打开对话框里的 autoFocus 输入框抢焦点。
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { Dialog } from '@beauty/design';

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
  const previousFocus = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    previousFocus.current = document.activeElement;
    return () => {
      const previous = previousFocus.current;
      if (previous instanceof HTMLElement) {
        previous.focus();
      }
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <Dialog
      open
      title={title}
      description={description}
      footer={footer}
      closable={false}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      className={cn('console-dialog', className)}
    >
      {children}
    </Dialog>
  );
}
