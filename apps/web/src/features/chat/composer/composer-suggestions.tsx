import { useEffect } from 'react';

import type { CaretAnchor } from './caret-rect';
import { SUGGESTIONS_ID, suggestionOptionId } from './suggestion-ids';
import type { ComposerGroup, ComposerOption } from './types';

type ComposerSuggestionsProps = {
  open: boolean;
  anchor: CaretAnchor | null;
  groups: ComposerGroup[];
  activeKey: string | null;
  label: string;
  emptyLabel: string;
  onPick: (option: ComposerOption) => void;
  onHover: (key: string) => void;
};

/**
 * 锚定光标的 suggestion 面板。
 * position: fixed 避免被消息列表的 overflow 裁切;小屏按 100vw - 32px 收窄。
 */
export function ComposerSuggestions({
  open,
  anchor,
  groups,
  activeKey,
  label,
  emptyLabel,
  onPick,
  onHover,
}: ComposerSuggestionsProps) {
  const total = groups.reduce((sum, group) => sum + group.options.length, 0);

  useEffect(() => {
    if (!open || !activeKey) return;
    document
      .getElementById(suggestionOptionId(activeKey))
      ?.scrollIntoView({ block: 'nearest' });
  }, [activeKey, open]);

  if (!open || !anchor) return null;

  const viewportWidth =
    typeof window === 'undefined' ? 1024 : window.innerWidth;
  const width = Math.min(380, viewportWidth - 32);
  const left = Math.max(8, Math.min(anchor.left, viewportWidth - width - 8));
  const opensUp = anchor.top > 300;
  return (
    <>
      <div className="sr-only" aria-live="polite">
        {total
          ? groups
              .map((group) => `${group.label} ${group.options.length} 项`)
              .join(';')
          : emptyLabel}
      </div>
      <div
        id={SUGGESTIONS_ID}
        role="listbox"
        aria-label={label}
        className="fixed z-50 max-h-[280px] overflow-y-auto rounded-[10px] border border-ha-border bg-ha-panel py-1 shadow-xl"
        style={{
          left,
          width,
          top: opensUp ? anchor.top - 6 : anchor.bottom + 6,
          transform: opensUp ? 'translateY(-100%)' : undefined,
        }}
      >
        {total === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] text-ha-muted">
            {emptyLabel}
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.key} role="group" aria-label={group.label}>
              <div className="sticky top-0 z-10 bg-ha-panel px-3 pt-2 pb-1 text-[11px] font-medium tracking-wide text-ha-dim">
                {group.label}
              </div>
              {group.options.map((option) => {
                const selected = option.key === activeKey;
                const Icon = option.icon;
                return (
                  <button
                    key={option.key}
                    id={suggestionOptionId(option.key)}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={selected}
                    aria-disabled={option.disabled || undefined}
                    className={`flex w-full items-start gap-2 border-l-2 px-3 py-2 text-left transition-colors ${
                      selected
                        ? 'border-ha-accent bg-ha-panel-2'
                        : 'border-transparent'
                    } ${option.disabled ? 'cursor-not-allowed opacity-40' : 'hover:bg-ha-panel-2'}`}
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => {
                      if (!option.disabled) onHover(option.key);
                    }}
                    onClick={() => {
                      if (!option.disabled) onPick(option);
                    }}
                  >
                    {Icon ? (
                      <Icon
                        aria-hidden="true"
                        className="mt-0.5 size-4 shrink-0 text-ha-muted"
                      />
                    ) : null}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-ha-text-body">
                        {option.label}
                      </span>
                      {option.description ? (
                        <span className="mt-0.5 block truncate text-[11px] text-ha-muted">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                    {option.current ? (
                      <span className="mt-0.5 shrink-0 text-[11px] text-ha-accent">
                        当前
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ))
        )}
        <div className="mt-1 border-t border-ha-border px-3 pt-2 pb-1 text-[11px] text-ha-dim">
          ↑↓ 选择 · Enter 确认 · Esc 关闭
        </div>
      </div>
    </>
  );
}
