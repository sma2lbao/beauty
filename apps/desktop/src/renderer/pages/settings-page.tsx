/**
 * 设置页:模型端点(OpenAI 兼容)、云端控制台地址、当前工作区。
 *
 * 表单控件用 @beauty/design 的 Field / Input(已跟随 --bd-* 暗色令牌),
 * 保存走 settings.update,成功后显示「设置已保存」。
 * 敏感信息只留在本机:API Key 通过密码框输入,不在这里做任何网络校验。
 */
import { Alert, Field, Input } from '@beauty/design';
import { FolderOpen, Save } from 'lucide-react';
import { useState } from 'react';

import { useDesktop } from '@/lib/desktop-context';

import { ConsoleButton, ConsolePanel, SectionLabel } from '@/components/ui';

export function SettingsPage() {
  const { bridge, state, actions } = useDesktop();
  const settings = state.settings;

  const [baseUrl, setBaseUrl] = useState(settings?.model.baseUrl ?? '');
  const [modelName, setModelName] = useState(settings?.model.model ?? '');
  const [apiKey, setApiKey] = useState(settings?.model.apiKey ?? '');
  const [cloudUrl, setCloudUrl] = useState(settings?.cloudConsoleUrl ?? '');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (!settings) return null;

  const save = async () => {
    const nextBaseUrl = baseUrl.trim();
    const nextModel = modelName.trim();
    if (!nextBaseUrl) {
      setError('请填写模型 Base URL');
      return;
    }
    if (!/^https?:\/\//i.test(nextBaseUrl)) {
      setError('Base URL 需要以 http:// 或 https:// 开头');
      return;
    }
    if (!nextModel) {
      setError('请填写模型名');
      return;
    }
    setError(null);
    setSaving(true);
    await actions.saveSettings({
      model: {
        baseUrl: nextBaseUrl,
        model: nextModel,
        apiKey: apiKey.trim(),
      },
      cloudConsoleUrl: cloudUrl.trim(),
    });
    setSaving(false);
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8">
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4">
        <header className="flex items-end justify-between gap-3">
          <div>
            <h1 className="text-[18px] font-semibold tracking-[-0.01em] text-ha-text">
              设置
            </h1>
            <p className="mt-1 font-mono text-[11px] text-ha-dim">
              本地模型端点 · 云端控制台 · 工作区
            </p>
          </div>
          <ConsoleButton
            variant="primary"
            onClick={() => void save()}
            disabled={saving}
          >
            <Save aria-hidden="true" className="h-3.5 w-3.5" />
            {saving ? '保存中…' : '保存设置'}
          </ConsoleButton>
        </header>

        {error ? (
          <p
            role="alert"
            className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] text-ha-danger"
          >
            {error}
          </p>
        ) : null}

        <ConsolePanel className="flex flex-col gap-4 p-4">
          <SectionLabel>模型端点</SectionLabel>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Base URL"
              htmlFor="model-base-url"
              hint="OpenAI 兼容端点,如 https://api.deepseek.com/v1"
            >
              <Input
                id="model-base-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.deepseek.com/v1"
                spellCheck={false}
                autoComplete="off"
              />
            </Field>
            <Field
              label="模型"
              htmlFor="model-name"
              hint="如 deepseek-chat、qwen2.5-coder"
            >
              <Input
                id="model-name"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="deepseek-chat"
                spellCheck={false}
                autoComplete="off"
              />
            </Field>
          </div>
          <Field
            label="API Key"
            htmlFor="model-api-key"
            hint="只保存在本机 userData,不会上传;本地 Ollama / vLLM 可留空"
          >
            <Input
              id="model-api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="sk-…"
              autoComplete="off"
            />
          </Field>
          <Alert variant="neutral">
            本地 Agent 通过 /chat/completions 流式调用该端点,tools
            需支持函数调用 (
            <code className="font-mono text-[11px]">
              list_files / read_file / write_file / run_command
            </code>
            )。
          </Alert>
        </ConsolePanel>

        <ConsolePanel className="flex flex-col gap-3 p-4">
          <SectionLabel>云端控制台</SectionLabel>
          <Field
            label="控制台地址"
            htmlFor="cloud-console-url"
            hint="云端模式下用系统浏览器打开这个地址(apps/web 构建产物或已部署站点)"
          >
            <Input
              id="cloud-console-url"
              value={cloudUrl}
              onChange={(event) => setCloudUrl(event.target.value)}
              placeholder="https://console.example.com"
              spellCheck={false}
              autoComplete="off"
            />
          </Field>
        </ConsolePanel>

        <ConsolePanel className="flex flex-col gap-3 p-4">
          <SectionLabel>工作区</SectionLabel>
          {state.workspace ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-ha-text-2">
                  {state.workspace.name}
                </p>
                <p
                  className="mt-0.5 truncate font-mono text-[11px] text-ha-faint"
                  title={state.workspace.root}
                >
                  {state.workspace.root}
                </p>
              </div>
              <ConsoleButton
                variant="secondary"
                onClick={() => void actions.chooseWorkspace()}
              >
                <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
                切换目录
              </ConsoleButton>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <p className="min-w-0 flex-1 text-[12px] text-ha-dim">
                尚未选择本地目录。工作区决定 Agent 能读写与执行命令的范围。
              </p>
              <ConsoleButton
                variant="secondary"
                onClick={() => void actions.chooseWorkspace()}
              >
                <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
                选择目录
              </ConsoleButton>
            </div>
          )}
        </ConsolePanel>

        <p className="pb-2 text-center font-mono text-[11px] text-ha-faint">
          beauty moon desktop v{bridge.appVersion} · {bridge.platform}
        </p>
      </div>
    </div>
  );
}
