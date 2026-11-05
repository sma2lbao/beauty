/**
 * ⌘K 命令面板。
 *
 * 视觉上复用侧栏导航的行高与选中色:命令面板是「另一条导航」,不是表单,
 * 所以没有标题栏、分区边框和按钮 —— 只有输入框、结果列表和键位提示。
 *
 * 关闭态直接返回 null:面板不在 DOM 时输入框不会抢焦点,也免去关闭后的清理。
 */
import { Dialog } from '@beauty/design';
import { Search } from 'lucide-react';
import { useEffect, useId } from 'react';

import { Kbd } from '@/components/console/ui';
import { cn } from '@/lib/utils';
import { useUiStore } from '@/stores/ui-store';

import {
  useCommandPaletteModel,
  type CommandPaletteItem,
} from './use-command-palette-model';

export function CommandPalette() {
  const open = useUiStore((state) => state.commandPaletteOpen);
  if (!open) {
    return null;
  }
  return <CommandPalettePanel />;
}

function CommandPalettePanel() {
  const model = useCommandPaletteModel();
  const idPrefix = useId().replace(/:/g, '');
  const listId = `${idPrefix}-list`;
  const optionId = (index: number) => `${idPrefix}-option-${index}`;
  const activeId =
    model.activeIndex >= 0 ? optionId(model.activeIndex) : undefined;

  useEffect(() => {
    if (!activeId) {
      return;
    }
    // 键盘移动时让选中项留在可视区内;浏览器不支持 scrollIntoView 的测试环境会跳过。
    document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' });
  }, [activeId]);

  return (
    <Dialog
      open
      aria-label="命令面板"
      closable={false}
      onOpenChange={(next) => {
        if (!next) {
          model.close();
        }
      }}
      className={cn(
        // 原生 <dialog> 自带 margin:auto 居中;只改上下外边距就能顶到视口上部。
        'mt-[12vh] mb-auto w-[560px] max-w-[calc(100vw-2rem)] max-h-[60vh] overflow-hidden border-ha-border-strong bg-ha-panel text-ha-text',
        '[&::backdrop]:bg-black/60',
        '[&_[data-slot=dialog-header]]:hidden',
        // 组件库的 body 默认带内边距与整块滚动,这里改造成「输入框固定 + 列表自滚」。
        '[&_[data-slot=dialog-body]]:flex [&_[data-slot=dialog-body]]:min-h-0 [&_[data-slot=dialog-body]]:flex-1 [&_[data-slot=dialog-body]]:flex-col [&_[data-slot=dialog-body]]:overflow-hidden [&_[data-slot=dialog-body]]:p-0',
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2.5 border-b border-ha-border px-4 py-3">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-ha-dim" />
          <input
            autoFocus
            type="text"
            role="combobox"
            aria-label="搜索会话、页面或动作"
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeId}
            placeholder="搜索会话、页面或动作…"
            value={model.query}
            onChange={(event) => model.setQuery(event.target.value)}
            onKeyDown={model.handleKeyDown}
            className="min-w-0 flex-1 bg-transparent text-[14px] text-ha-text outline-none placeholder:text-ha-dim"
          />
          <Kbd>esc</Kbd>
        </div>

        <div
          id={listId}
          role="listbox"
          aria-label="命令结果"
          className="min-h-0 flex-1 overflow-y-auto px-2 py-2"
        >
          {model.groups.map((group) => {
            if (group.items.length === 0 && !group.placeholder) {
              return null;
            }
            return (
              <div
                key={group.id}
                role="group"
                aria-label={group.label}
                className="pb-1"
              >
                <div
                  aria-hidden="true"
                  className="px-2.5 pb-1 pt-2 text-[11px] font-semibold text-ha-dim"
                >
                  {group.label}
                </div>
                {group.items.map((item) => {
                  // 分组只影响展示,选项在主列表里的坐标才是选中态与 aria 的依据。
                  const index = model.items.indexOf(item);
                  return (
                    <CommandPaletteOption
                      key={item.id}
                      item={item}
                      id={optionId(index)}
                      selected={index === model.activeIndex}
                      onHover={() => model.setActiveIndex(index)}
                      onSelect={() => model.execute(item)}
                    />
                  );
                })}
                {group.items.length === 0 && group.placeholder ? (
                  <p className="px-2.5 py-1.5 text-[12px] text-ha-dim">
                    {group.placeholder}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>

        <div className="flex shrink-0 items-center gap-3 border-t border-ha-border px-4 py-2 text-[11px] text-ha-dim">
          <span className="flex items-center gap-1">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            <span>选择</span>
          </span>
          <span aria-hidden="true">·</span>
          <span className="flex items-center gap-1">
            <Kbd>↵</Kbd>
            <span>打开</span>
          </span>
        </div>
      </div>
    </Dialog>
  );
}

function CommandPaletteOption({
  item,
  id,
  selected,
  onHover,
  onSelect,
}: {
  item: CommandPaletteItem;
  id: string;
  selected: boolean;
  onHover: () => void;
  onSelect: () => void;
}) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={selected}
      aria-disabled={item.disabled ? true : undefined}
      // 选项不给 tabIndex:焦点始终留在输入框,由 aria-activedescendant 播报选中项。
      onMouseEnter={onHover}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onSelect}
      className={cn(
        'flex h-9 items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors',
        item.disabled ? 'cursor-not-allowed text-ha-faint' : 'cursor-pointer',
        !item.disabled && selected
          ? 'bg-[#1c1d1f] text-ha-text'
          : !item.disabled &&
              'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
      )}
    >
      <span className="shrink-0 [&>svg]:h-4 [&>svg]:w-4">{item.icon}</span>
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {item.hint ? (
        <span className="shrink-0 font-mono text-[10px] text-ha-faint">
          {item.hint}
        </span>
      ) : null}
    </div>
  );
}
