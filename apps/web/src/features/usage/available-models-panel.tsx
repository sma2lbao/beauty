/**
 * 可用模型卡片:llm-gateway 模型目录与默认模型标记。
 */
import { ConsolePanel, ToneBadge } from '@/components/console/ui';
import type { ModelInfo } from '@/lib/insights-api';

export function AvailableModelsPanel({
  models,
  defaultModel,
}: {
  models: ModelInfo[];
  defaultModel: string | undefined;
}) {
  return (
    <ConsolePanel className="p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-semibold text-ha-text-2">可用模型</h2>
        <span className="font-mono text-[11px] text-ha-faint">llm-gateway</span>
      </div>
      {models.length ? (
        <ul className="mt-3 space-y-2">
          {models.map((model) => (
            <li
              key={model.id}
              className="flex items-center justify-between gap-2 rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
            >
              <span className="truncate font-mono text-[12px] text-ha-text-2">
                {model.id}
              </span>
              {model.id === defaultModel ? (
                <ToneBadge tone="success">默认</ToneBadge>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-[12px] text-ha-dim">
          模型列表暂不可用(检查 llm-gateway)。
        </p>
      )}
    </ConsolePanel>
  );
}
