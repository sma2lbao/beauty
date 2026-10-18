/**
 * BYOK 占位(M0-11 本地版):只做加密存取与掩码展示,暂不参与模型调用。
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
} from '@beauty/design';

import { insightsApi } from '@/lib/insights-api';

const PROVIDERS = ['openai', 'anthropic', 'google'] as const;

export function ByokPanel() {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<string>('openai');
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState<string | null>(null);

  const keysQuery = useQuery({ queryKey: ['byok'], queryFn: insightsApi.byok });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['byok'] });

  const saveMutation = useMutation({
    mutationFn: () => insightsApi.saveByok(provider, apiKey.trim()),
    onSuccess: async () => {
      setApiKey('');
      setError(null);
      await refresh();
    },
    onError: (failure) => setError((failure as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (target: string) => insightsApi.deleteByok(target),
    onSuccess: refresh,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>BYOK(自带模型密钥)</CardTitle>
        <CardDescription>
          应用层加密存储,只显示尾号掩码;暂不参与模型调用(占位)。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="提供商" htmlFor="byok-provider">
            <Select
              id="byok-provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              {PROVIDERS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="API Key" htmlFor="byok-key">
            <Input
              id="byok-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="sk-…(保存后只显示尾号)"
            />
          </Field>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || apiKey.trim().length < 8}
        >
          {saveMutation.isPending ? '保存中…' : '保存密钥'}
        </Button>

        {keysQuery.data?.length ? (
          <ul className="space-y-2">
            {keysQuery.data.map((item) => (
              <li
                key={item.provider}
                className="flex items-center justify-between gap-3 rounded-md border border-ha-border bg-ha-panel-2 px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="block font-mono text-[12px] text-ha-text-2">
                    {item.provider}
                  </span>
                  <span className="font-mono text-[11px] text-ha-dim">
                    {item.masked_key}
                  </span>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => deleteMutation.mutate(item.provider)}
                  disabled={deleteMutation.isPending}
                >
                  删除
                </Button>
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
