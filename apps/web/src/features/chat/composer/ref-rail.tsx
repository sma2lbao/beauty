/**
 * 引用轨:输入区上方常驻的已选上下文与连接器芯片。
 *
 * 内容引用(知识库/交付物/沙箱文件)删除芯片会同步删正文 token;
 * 连接器是能力绑定,删除芯片只解绑,不追删正文里已经写下的可读 token。
 */
import { Package, X } from 'lucide-react';

import type { ChatContextRef } from '../use-chat-stream';
import {
  CONTEXT_KIND_ICONS,
  CONTEXT_KIND_LABELS,
  refKey,
} from './context-kinds';
import type { ComposerItem } from './types';

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
  if (!refs.length && !selectedConnectors.length) return null;
  const labelOf = (ref: ChatContextRef) =>
    ref.title ||
    items.find((item) => refKey(item) === refKey(ref))?.label ||
    ref.id;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {refs.map((ref) => {
        const Icon = CONTEXT_KIND_ICONS[ref.kind];
        const label = labelOf(ref);
        return (
          <span
            key={refKey(ref)}
            title={`${CONTEXT_KIND_LABELS[ref.kind]} · ${label}`}
            className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-text-2"
          >
            <Icon className="h-3 w-3 shrink-0 text-ha-muted" />
            <span className="truncate">{label}</span>
            <button
              type="button"
              aria-label={`移除引用 ${label}`}
              onClick={() => onRemoveRef(ref)}
              className="text-ha-dim hover:text-ha-text"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {selectedConnectors.map((provider) => (
        <span
          key={`connector:${provider}`}
          title={`连接器 · ${connectorName(provider)}`}
          className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-text-2"
        >
          <Package className="h-3 w-3 shrink-0 text-ha-muted" />
          <span className="truncate">{connectorName(provider)}</span>
          <button
            type="button"
            aria-label={`移除连接器 ${connectorName(provider)}`}
            onClick={() => onRemoveConnector(provider)}
            className="text-ha-dim hover:text-ha-text"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
    </div>
  );
}
