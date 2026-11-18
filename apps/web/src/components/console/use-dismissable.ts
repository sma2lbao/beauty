/**
 * 「点外部 / Escape 收起」行为:给自绘浮层(通知面板等)复用。
 *
 * 打开时监听 document:点外部只收起(不抢焦点),Escape 收起并把焦点还给触发器
 * ——键盘用户收起后不该掉到 body 上。下拉菜单本身由 @beauty/design 的
 * DropdownMenu 负责,这里只留给不走菜单语义的自定义面板。
 */
import type { RefObject } from 'react';
import { useEffect } from 'react';

export function useDismissable({
  open,
  onClose,
  containerRef,
  triggerRef,
}: {
  open: boolean;
  /** 收起回调。内联箭头函数也可以:监听器每次重开都会重挂,代价可忽略。 */
  onClose: () => void;
  /** 包住触发器与面板的根节点,点在它内部不关闭。 */
  containerRef: RefObject<HTMLElement | null>;
  /** Escape 收起后接收焦点的触发器。 */
  triggerRef?: RefObject<HTMLElement | null>;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      const root = containerRef.current;
      if (root && !root.contains(event.target as Node)) {
        onClose();
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        triggerRef?.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onClose, containerRef, triggerRef]);
}
