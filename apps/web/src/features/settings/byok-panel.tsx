/**
 * BYOK 面板(M0-11 本地版):应用层加密存取与掩码展示;密钥已参与模型调用
 * (网关按租户解析,见 docs/design/byok.md)。
 *
 * 每个 provider 除密钥外还维护一份自定义模型清单(每行一个以 <provider>/ 开头的
 * 模型 id,整体替换):保存后网关白名单与 GET /api/v1/models 会带上这些模型,
 * 聊天等模型选择器即可选到。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Select,
  Textarea,
} from '@beauty/design';

import { insightsApi, type BYOKKey } from '@/lib/insights-api';
import { queryKeys } from '@/lib/query-keys';

/** 预设提供商;末尾「自定义…」允许填写自有/兼容网关的 provider 名称。 */
const PROVIDERS = [
  'openai',
  'anthropic',
  'google',
  'deepseek',
  'groq',
  'mistral',
  'xai',
] as const;

/** Select 里代表「自定义」的哨兵值(不会与合法 provider 冲突)。 */
const CUSTOM_PROVIDER = '__custom__';

/** 与 account-service 契约一致:小写字母/数字开头,只含 [a-z0-9._-],总长 ≤32。 */
const PROVIDER_PATTERN = /^[a-z0-9][a-z0-9._-]{0,31}$/;
/** 与 account-service 契约一致(首字符是字母或数字)。 */
const MODEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/+-]*$/;
const MAX_MODELS = 32;
const MAX_MODEL_LENGTH = 128;
const MIN_KEY_LENGTH = 8;

/** 各预设 provider 的模型 id 示例(用作 Textarea 的 placeholder)。 */
const MODEL_EXAMPLES: Record<string, string> = {
  openai: 'openai/gpt-4o-mini',
  anthropic: 'anthropic/claude-sonnet-4',
  google: 'google/gemini-2.0-flash',
  deepseek: 'deepseek/deepseek-chat',
  groq: 'groq/llama-3.3-70b-versatile',
  mistral: 'mistral/mistral-large-latest',
  xai: 'xai/grok-2-latest',
};

/** 按行解析模型清单:去首尾空白、丢空行、去重保序(与后端语义一致)。 */
function parseModels(text: string): string[] {
  const seen = new Set<string>();
  const models: string[] = [];
  for (const rawLine of text.split('\n')) {
    const model = rawLine.trim();
    if (!model || seen.has(model)) continue;
    seen.add(model);
    models.push(model);
  }
  return models;
}

/** 返回模型清单的第一条错误;空清单合法(等于清空自定义模型)。 */
function validateModels(text: string, provider: string): string | null {
  if (!text.trim()) return null;
  if (!provider) return '请先填写提供商名称,再填写自定义模型';
  const models = parseModels(text);
  if (models.length > MAX_MODELS)
    return '最多配置 ' + MAX_MODELS + ' 个模型 ID';
  for (const model of models) {
    if (!model.startsWith(provider + '/')) {
      return '模型 ID 必须以 ' + provider + '/ 开头';
    }
    if (model.length > MAX_MODEL_LENGTH) {
      return '单个模型 ID 不能超过 ' + MAX_MODEL_LENGTH + ' 个字符';
    }
    if (!MODEL_PATTERN.test(model)) return '模型 ID 含非法字符:' + model;
  }
  return null;
}

type ByokKeyRowProps = {
  item: BYOKKey;
  onEdit: (item: BYOKKey) => void;
  onDelete: (provider: string) => void;
  deletePending: boolean;
};

/** 已保存条目:provider / masked_key / 自定义模型 chips + 编辑/删除入口。 */
function ByokKeyRow({
  item,
  onEdit,
  onDelete,
  deletePending,
}: ByokKeyRowProps) {
  return (
    <div className="rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block font-mono text-[12px] text-ha-text-2">
            {item.provider}
          </span>
          <span className="font-mono text-[11px] text-ha-dim">
            {item.masked_key}
          </span>
        </span>
        <span className="flex shrink-0 gap-1.5">
          <Button variant="outline" size="sm" onClick={() => onEdit(item)}>
            编辑
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onDelete(item.provider)}
            disabled={deletePending}
          >
            删除
          </Button>
        </span>
      </div>
      {item.models.length ? (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {item.models.map((model) => (
            <li
              key={model}
              className="rounded border border-ha-border bg-ha-code px-1.5 py-0.5 font-mono text-[11px] text-ha-text-2"
            >
              {model}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[11px] text-muted-foreground">
          未配置自定义模型
        </p>
      )}
    </div>
  );
}

export function ByokPanel() {
  const queryClient = useQueryClient();
  const [providerChoice, setProviderChoice] = useState<string>('openai');
  const [customProvider, setCustomProvider] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [modelsText, setModelsText] = useState('');
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const keysQuery = useQuery({
    queryKey: queryKeys.byok,
    queryFn: insightsApi.byok,
  });
  const keys = keysQuery.data ?? [];

  const isCustom = providerChoice === CUSTOM_PROVIDER;
  const provider = (isCustom ? customProvider : providerChoice).trim();
  // 编辑已有条目时锁定 provider:PUT 路径就是它,且「留空密钥」只对已有记录成立。
  const providerLocked = editingProvider !== null;
  const providerError = !isCustom
    ? null
    : !provider
      ? '请填写提供商名称'
      : PROVIDER_PATTERN.test(provider)
        ? null
        : '仅支持小写字母、数字、点、下划线、连字符,以字母或数字开头,≤32 字符';
  const modelsError = validateModels(modelsText, provider);
  const keyOptional =
    providerLocked || keys.some((item) => item.provider === provider);
  const keyTooShort = !keyOptional && apiKey.trim().length < MIN_KEY_LENGTH;
  const canSave = !providerError && !modelsError && !keyTooShort;

  const refreshModels = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.byok }),
      queryClient.invalidateQueries({ queryKey: queryKeys.chatModels }),
    ]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const trimmedKey = apiKey.trim();
      return insightsApi.saveByok(
        provider,
        trimmedKey ? trimmedKey : null,
        parseModels(modelsText),
      );
    },
    onSuccess: async () => {
      setApiKey('');
      setEditingProvider(null);
      setError(null);
      await refreshModels();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (target: string) => insightsApi.deleteByok(target),
    onSuccess: refreshModels,
  });

  const startEdit = (item: BYOKKey) => {
    const isPreset = PROVIDERS.some((preset) => preset === item.provider);
    setProviderChoice(isPreset ? item.provider : CUSTOM_PROVIDER);
    setCustomProvider(isPreset ? '' : item.provider);
    setEditingProvider(item.provider);
    setModelsText(item.models.join('\n'));
    setApiKey('');
    setError(null);
  };

  const cancelEdit = () => {
    setEditingProvider(null);
    setApiKey('');
    setModelsText('');
    setError(null);
  };

  const modelsPlaceholder = !provider
    ? '先填写提供商名称'
    : (MODEL_EXAMPLES[provider] ?? provider + '/your-model-id');

  return (
    <Card>
      <CardHeader>
        <CardTitle>BYOK(自带模型密钥)</CardTitle>
        <CardDescription>
          应用层加密存储,只显示尾号掩码;已参与模型调用(网关按租户解析)。
          自定义模型清单会并入 /api/v1/models,保存后即可在模型选择器里选到。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="提供商"
            htmlFor="byok-provider"
            hint={providerLocked ? '编辑已有配置时不能更换提供商' : undefined}
          >
            <Select
              id="byok-provider"
              value={providerChoice}
              disabled={providerLocked}
              onChange={(event) => setProviderChoice(event.target.value)}
            >
              {PROVIDERS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
              <option value={CUSTOM_PROVIDER}>自定义…</option>
            </Select>
          </Field>
          <Field
            label="API Key"
            htmlFor="byok-key"
            hint={keyOptional ? 'API Key 留空 = 不更换密钥' : undefined}
          >
            <Input
              id="byok-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={
                keyOptional ? '留空 = 不更换密钥' : 'sk-…(保存后只显示尾号)'
              }
            />
          </Field>
        </div>

        {isCustom ? (
          <Field
            label="提供商名称"
            htmlFor="byok-custom-provider"
            hint="仅小写,如 my-gateway"
            error={providerError}
          >
            <Input
              id="byok-custom-provider"
              value={customProvider}
              disabled={providerLocked}
              onChange={(event) => setCustomProvider(event.target.value)}
              placeholder="my-gateway"
            />
          </Field>
        ) : null}

        <Field
          label="自定义模型"
          htmlFor="byok-models"
          hint="每行一个模型 ID;留空 = 清空自定义模型"
          error={modelsError}
        >
          <Textarea
            id="byok-models"
            rows={4}
            className="font-mono text-[12px]"
            value={modelsText}
            invalid={modelsError !== null}
            onChange={(event) => setModelsText(event.target.value)}
            placeholder={modelsPlaceholder}
          />
        </Field>

        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || !canSave}
          >
            {saveMutation.isPending
              ? '保存中…'
              : editingProvider
                ? '保存修改'
                : '保存配置'}
          </Button>
          {providerLocked ? (
            <Button variant="outline" onClick={cancelEdit}>
              取消编辑
            </Button>
          ) : null}
        </div>

        {keys.length ? (
          <ul className="space-y-2">
            {keys.map((item) => (
              <li key={item.provider}>
                <ByokKeyRow
                  item={item}
                  onEdit={startEdit}
                  onDelete={(target) => deleteMutation.mutate(target)}
                  deletePending={deleteMutation.isPending}
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            还没有保存 BYOK 密钥。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
