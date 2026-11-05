/**
 * BYOK 面板交互契约(W7 自定义 provider / 自定义模型清单):
 * - 保存时把 provider、密钥与整体替换的模型清单交给 saveByok;
 * - 自定义 provider 即时校验;自定义模型非空行必须以 <provider>/ 开头;
 * - 编辑回填 provider 与模型,API Key 留空传 null;
 * - 已保存列表展示 masked_key 与模型 chips,无模型给出明确文案;
 * - 保存成功后同时失效 byok 与 chat-models 缓存。
 *
 * 数据层整层打桩(@/lib/insights-api),QueryClient 用真实实例观察 invalidate。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { insightsApi, type BYOKKey } from '@/lib/insights-api';
import { queryKeys } from '@/lib/query-keys';

import { ByokPanel } from './byok-panel';

vi.mock('@/lib/insights-api', () => ({
  insightsApi: {
    byok: vi.fn(),
    saveByok: vi.fn(),
    deleteByok: vi.fn(),
  },
}));

const savedKey = (overrides: Partial<BYOKKey> = {}): BYOKKey => ({
  provider: 'openai',
  masked_key: 'sk-…1234',
  key_hint: '1234',
  updated_at: '2026-11-05T00:00:00Z',
  models: [],
  ...overrides,
});

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <ByokPanel />
    </QueryClientProvider>,
  );
  return { client, invalidate };
}

const saveButton = () => screen.getByRole('button', { name: /保存/ });

beforeEach(() => {
  vi.mocked(insightsApi.byok).mockResolvedValue([]);
  vi.mocked(insightsApi.saveByok).mockResolvedValue(savedKey());
  vi.mocked(insightsApi.deleteByok).mockResolvedValue(undefined);
});

describe('ByokPanel', () => {
  it('保存时提交 provider、密钥与整体替换的模型清单', async () => {
    const { invalidate } = renderPanel();
    await screen.findByText('还没有保存 BYOK 密钥。');

    const modelsInput = screen.getByLabelText('自定义模型');
    // placeholder 按当前 provider 生成
    expect(modelsInput.getAttribute('placeholder')).toBe('openai/gpt-4o-mini');

    fireEvent.change(screen.getByLabelText('API Key'), {
      target: { value: 'sk-secret-1234' },
    });
    // 去空白、丢空行、去重保序后再提交
    fireEvent.change(modelsInput, {
      target: {
        value: '  openai/gpt-4o-mini \n\n openai/gpt-4o \n openai/gpt-4o-mini ',
      },
    });
    fireEvent.click(saveButton());

    await waitFor(() =>
      expect(insightsApi.saveByok).toHaveBeenCalledWith(
        'openai',
        'sk-secret-1234',
        ['openai/gpt-4o-mini', 'openai/gpt-4o'],
      ),
    );
    // 保存后同时失效 BYOK 列表与模型目录(聊天选择器随即重查)
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.byok }),
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.chatModels,
    });
  });

  it('自定义 provider 即时校验:非法值报错并禁用保存,合法小写通过', async () => {
    renderPanel();
    await screen.findByText('还没有保存 BYOK 密钥。');
    fireEvent.change(screen.getByLabelText('API Key'), {
      target: { value: 'sk-secret-1234' },
    });
    fireEvent.change(screen.getByLabelText('提供商'), {
      target: { value: '__custom__' },
    });

    // 刚切到自定义时空值即报错(错误态下 Field 以错误文案替掉 hint)
    expect(screen.getByText('请填写提供商名称')).toBeInTheDocument();

    const nameInput = screen.getByLabelText('提供商名称');
    fireEvent.change(nameInput, { target: { value: 'My-Gateway' } });
    expect(screen.getByText(/仅支持小写字母/)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: 'my-gateway' } });
    expect(screen.queryByText(/仅支持小写字母/)).not.toBeInTheDocument();
    // 无错误时给出小写格式提示
    expect(screen.getByText('仅小写,如 my-gateway')).toBeInTheDocument();
    expect(saveButton()).not.toBeDisabled();
  });

  it('自定义模型非空行必须以 provider/ 开头,非法时禁用保存并显示错误', async () => {
    renderPanel();
    await screen.findByText('还没有保存 BYOK 密钥。');
    fireEvent.change(screen.getByLabelText('API Key'), {
      target: { value: 'sk-secret-1234' },
    });

    fireEvent.change(screen.getByLabelText('自定义模型'), {
      target: { value: 'gpt-4o-mini' },
    });
    expect(screen.getByText('模型 ID 必须以 openai/ 开头')).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText('自定义模型'), {
      target: { value: 'openai/gpt-4o-mini' },
    });
    expect(screen.queryByText(/必须以 openai\//)).not.toBeInTheDocument();
    expect(saveButton()).not.toBeDisabled();
  });

  it('provider 未填写时提示先填 provider', async () => {
    renderPanel();
    await screen.findByText('还没有保存 BYOK 密钥。');
    fireEvent.change(screen.getByLabelText('提供商'), {
      target: { value: '__custom__' },
    });
    fireEvent.change(screen.getByLabelText('自定义模型'), {
      target: { value: 'openai/gpt-4o-mini' },
    });
    expect(
      screen.getByText('请先填写提供商名称,再填写自定义模型'),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it('编辑已有条目回填 provider 与模型,API Key 留空时传 null', async () => {
    vi.mocked(insightsApi.byok).mockResolvedValue([
      savedKey({
        provider: 'anthropic',
        masked_key: 'sk-…9f9f',
        models: ['anthropic/claude-sonnet-4'],
      }),
    ]);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }));

    expect((screen.getByLabelText('提供商') as HTMLSelectElement).value).toBe(
      'anthropic',
    );
    expect(
      (screen.getByLabelText('自定义模型') as HTMLTextAreaElement).value,
    ).toBe('anthropic/claude-sonnet-4');
    expect((screen.getByLabelText('API Key') as HTMLInputElement).value).toBe(
      '',
    );
    expect(screen.getByText('API Key 留空 = 不更换密钥')).toBeInTheDocument();

    fireEvent.click(saveButton());
    await waitFor(() =>
      expect(insightsApi.saveByok).toHaveBeenCalledWith('anthropic', null, [
        'anthropic/claude-sonnet-4',
      ]),
    );
  });

  it('编辑自定义 provider 的条目回填 provider 名称', async () => {
    vi.mocked(insightsApi.byok).mockResolvedValue([
      savedKey({ provider: 'my-gateway', models: ['my-gateway/llama-3'] }),
    ]);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }));

    expect((screen.getByLabelText('提供商') as HTMLSelectElement).value).toBe(
      '__custom__',
    );
    expect(
      (screen.getByLabelText('提供商名称') as HTMLInputElement).value,
    ).toBe('my-gateway');

    fireEvent.click(saveButton());
    await waitFor(() =>
      expect(insightsApi.saveByok).toHaveBeenCalledWith('my-gateway', null, [
        'my-gateway/llama-3',
      ]),
    );
  });

  it('已保存列表展示 masked_key 与模型 chips,无模型显示未配置文案', async () => {
    vi.mocked(insightsApi.byok).mockResolvedValue([
      savedKey({
        provider: 'openai',
        masked_key: 'sk-…abcd',
        models: ['openai/gpt-4o-mini', 'openai/gpt-4o'],
      }),
      savedKey({ provider: 'google', masked_key: 'AIza…wxyz', models: [] }),
    ]);
    renderPanel();

    expect(await screen.findByText('openai/gpt-4o-mini')).toBeInTheDocument();
    expect(screen.getByText('openai/gpt-4o')).toBeInTheDocument();
    expect(screen.getByText('sk-…abcd')).toBeInTheDocument();

    // provider 名在 select 选项里也有一份,断言时限定到列表行内
    const googleRow = screen.getByText('AIza…wxyz').closest('li');
    expect(googleRow).not.toBeNull();
    expect(
      within(googleRow as HTMLElement).getByText('google'),
    ).toBeInTheDocument();
    expect(
      within(googleRow as HTMLElement).getByText('未配置自定义模型'),
    ).toBeInTheDocument();
  });
});
