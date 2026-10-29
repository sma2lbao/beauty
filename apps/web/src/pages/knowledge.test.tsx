import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { KnowledgeDocument } from '@/lib/knowledge-api';
import { knowledgeApi } from '@/lib/knowledge-api';

import { KnowledgePage } from './knowledge';

vi.mock('@/lib/knowledge-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/knowledge-api')>(
      '@/lib/knowledge-api',
    );
  return {
    ...actual,
    knowledgeApi: {
      documents: vi.fn(),
      ingest: vi.fn(),
      remove: vi.fn(),
      search: vi.fn(),
    },
  };
});

// 鉴权面板在 dev 模式下会调用 auth,这里整体替换掉(与本页测试无关)
vi.mock('@/features/auth/auth-bar', () => ({
  AuthBar: () => <div data-testid="auth-bar" />,
}));

function makeDocument(
  overrides: Partial<KnowledgeDocument> = {},
): KnowledgeDocument {
  return {
    id: 'doc-1',
    title: '客户 A 合同备忘',
    source: 'manual',
    size_bytes: 48,
    chunk_count: 1,
    embedding_model: 'hashing-256',
    created_at: '2026-10-13T09:00:00+00:00',
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <KnowledgePage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(knowledgeApi.documents).mockResolvedValue([]);
});

describe('KnowledgePage', () => {
  it('展示已入库资料(片段数/模型/来源)', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([makeDocument()]);

    renderPage();

    expect(await screen.findByText('客户 A 合同备忘')).toBeTruthy();
    expect(
      screen.getByText(/1 个片段 · 48 B · hashing-256 · manual/),
    ).toBeTruthy();
  });

  it('没有资料时给出引导文案', async () => {
    renderPage();

    expect(
      await screen.findByText('还没有资料:先在上方入库一篇。'),
    ).toBeTruthy();
  });

  it('入库提交标题与正文,并刷新列表', async () => {
    vi.mocked(knowledgeApi.ingest).mockResolvedValue(
      makeDocument({ title: '预算说明' }),
    );
    vi.mocked(knowledgeApi.documents)
      .mockResolvedValueOnce([])
      .mockResolvedValue([makeDocument({ title: '预算说明' })]);

    renderPage();
    await screen.findByText('还没有资料:先在上方入库一篇。');

    fireEvent.change(screen.getByLabelText('资料标题'), {
      target: { value: '预算说明' },
    });
    fireEvent.change(screen.getByLabelText('正文'), {
      target: { value: '本季度预算上限 50 万' },
    });
    fireEvent.click(screen.getByRole('button', { name: '入库' }));

    await waitFor(() => {
      expect(knowledgeApi.ingest).toHaveBeenCalledWith({
        title: '预算说明',
        content: '本季度预算上限 50 万',
        source: 'manual',
      });
    });
    expect(await screen.findByText('已入库 1 个片段')).toBeTruthy();
    // 列表确实刷新了(断言行为,而不是取数次数——后者是实现细节)
    expect(await screen.findByText('预算说明')).toBeTruthy();
  });

  it('检索展示命中与提供方信息', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([]);
    vi.mocked(knowledgeApi.search).mockResolvedValue({
      query: '合同评审',
      provider: 'local',
      model: 'hashing-256',
      hits: [
        {
          chunk_id: 'c1',
          document_id: 'doc-1',
          title: '客户 A 合同备忘',
          ordinal: 0,
          content: '合同评审安排在周四下午两点。',
          score: 0.511101,
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByLabelText('检索问题'), {
      target: { value: '合同评审' },
    });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByText('合同评审安排在周四下午两点。')).toBeTruthy();
    expect(screen.getByText('《客户 A 合同备忘》#0')).toBeTruthy();
    expect(screen.getByText('相似度 0.511')).toBeTruthy();
    expect(screen.getByText(/提供方 local \/ 模型 hashing-256/)).toBeTruthy();
  });

  it('检索无命中时给出提示', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([]);
    vi.mocked(knowledgeApi.search).mockResolvedValue({
      query: '不存在',
      provider: 'local',
      model: 'hashing-256',
      hits: [],
    });

    renderPage();

    fireEvent.change(screen.getByLabelText('检索问题'), {
      target: { value: '不存在' },
    });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(
      await screen.findByText('没有命中:换一种问法,或先入库相关资料。'),
    ).toBeTruthy();
  });

  it('删除按钮调用删除接口', async () => {
    vi.mocked(knowledgeApi.documents).mockResolvedValue([makeDocument()]);
    vi.mocked(knowledgeApi.remove).mockResolvedValue(undefined);

    renderPage();
    await screen.findByText('客户 A 合同备忘');

    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(knowledgeApi.remove).toHaveBeenCalledWith('doc-1');
    });
  });
});
