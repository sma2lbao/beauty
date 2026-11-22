/**
 * 引用轨:输入区上方常驻的已选上下文与连接器芯片。
 *
 * 内容引用(知识库/交付物/沙箱文件)删除芯片会同步删正文 token;
 * 连接器是能力绑定,删除芯片只解绑,不追删正文里已经写下的可读 token。
 * 超过一行可见数量时收成 "+N",点开再展开(设计 §5.6 两行收纳)。
 */
import { Package, X } from 'lucide-react';
import { useState } from 'react';

import type { ChatContextRef } from '../use-chat-stream';
import {
  CONTEXT_KIND_ICONS,
  CONTEXT_KIND_LABELS,
  refKey,
} from './context-kinds';
import type { ComposerItem } from './types';

/** 收起时可见的芯片数:约两行(单行 5–6 个,按窄屏保守取值)。 */
const MAX_VISIBLE = 6;

type RailEntry = {
  key: string;
  label: string;
  title: string;
  removeLabel: string;
  Icon: typeof Package;
  onRemove: () => void;
};

export function RefRail({
  refs,
  items,
  selectedConnectors,
  connectorName,
  onRemoveRef,
  onRemoveConnector,
}: {
  refs: ChatContextRef[];
  items: ComposerItem[];
  selectedConnectors: string[];
  connectorName: (provider: string) => string;
  onRemoveRef: (ref: ChatContextRef) => void;
  onRemoveConnector: (provider: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!refs.length && !selectedConnectors.length) return null;
  const labelOf = (ref: ChatContextRef) =>
    ref.title ||
    items.find((item) => refKey(item) === refKey(ref))?.label ||
    ref.id;
  const entries: RailEntry[] = [
    ...refs.map((ref) => {
      const label = labelOf(ref);
      return {
        key: refKey(ref),
        label,
        title: `${CONTEXT_KIND_LABELS[ref.kind]} · ${label}`,
        removeLabel: `移除引用 ${label}`,
        Icon: CONTEXT_KIND_ICONS[ref.kind],
        onRemove: () => onRemoveRef(ref),
      };
    }),
    ...selectedConnectors.map((provider) => ({
      key: `connector:${provider}`,
      label: connectorName(provider),
      title: `连接器 · ${connectorName(provider)}`,
      removeLabel: `移除连接器 ${connectorName(provider)}`,
      Icon: Package,
      onRemove: () => onRemoveConnector(provider),
    })),
  ];
  const visible = expanded ? entries : entries.slice(0, MAX_VISIBLE);
  const hidden = entries.length - visible.length;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {visible.map((entry) => {
        const Icon = entry.Icon;
        return (
          <span
            key={entry.key}
            title={entry.title}
            className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-text-2"
          >
            <Icon className="h-3 w-3 shrink-0 text-ha-muted" />
            <span className="truncate">{entry.label}</span>
            <button
              type="button"
              aria-label={entry.removeLabel}
              onClick={entry.onRemove}
              className="text-ha-dim hover:text-ha-text"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {hidden > 0 ? (
        <button
          type="button"
          aria-label={`展开其余 ${hidden} 个引用`}
          onClick={() => setExpanded(true)}
          className="rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-muted hover:text-ha-text"
        >
          +{hidden}
        </button>
      ) : null}
      {expanded && entries.length > MAX_VISIBLE ? (
        <button
          type="button"
          aria-label="收起引用"
          onClick={() => setExpanded(false)}
          className="rounded-md px-2 py-0.5 font-mono text-[11px] text-ha-dim hover:text-ha-text"
        >
          收起
        </button>
      ) : null}
    </div>
  );
}
