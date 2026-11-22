import type { InfiniteData } from '@tanstack/react-query';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { runsBase } from '@/config/env';
import type { Run, RunPage } from '@/lib/runs-types';
import { queryKeys } from '@/lib/query-keys';
import type { ConnectSseOptions } from '@/lib/sse';
import {
  RUN_STATE_EVENT,
  getRunsStreamStatus,
  parseRunStateFrame,
  upsertRun,
  useRunsStream,
} from '@/lib/runs-stream';

const { connectSseMock } = vi.hoisted(() => ({ connectSseMock: vi.fn() }));

vi.mock('@/lib/sse', async () => {
  const actual = await vi.importActual<typeof import('@/lib/sse')>('@/lib/sse');
  return { ...actual, connectSse: connectSseMock };
});

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    title: '整理本周日程成周报',
    goal: '汇总本周日程,生成周报草稿',
    status: 'awaiting_approval',
    rounds: 0,
    attempts: 1,
    acceptance_criteria: [],
    status_reason: null,
    parent_run_id: null,
    conversation_id: 'c1',
    created_at: '2026-10-13T10:00:00+00:00',
    ...overrides,
  };
}

function pageOf(items: Run[]): InfiniteData<RunPage> {
  return { pages: [{ items, next_cursor: null }], pageParams: [undefined] };
}

function stateFrame(run: Run, cursor = 'c1') {
  return {
    event: RUN_STATE_EVENT,
    data: JSON.stringify({ cursor, run }),
    raw: '',
  };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

beforeEach(() => {
  connectSseMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('parseRunStateFrame', () => {
  it('取出游标与 run 摘要', () => {
    const parsed = parseRunStateFrame(stateFrame(makeRun(), 'c9'));
    expect(parsed?.cursor).toBe('c9');
    expect(parsed?.run.id).toBe('run-1');
  });

  it('坏 JSON 或缺 run 时返回 null(跳过该帧)', () => {
    expect(
      parseRunStateFrame({ event: RUN_STATE_EVENT, data: 'not-json' }),
    ).toBeNull();
    expect(
      parseRunStateFrame({ event: RUN_STATE_EVENT, data: '{"cursor":"c1"}' }),
    ).toBeNull();
  });
});

describe('upsertRun', () => {
  it('未命中按 id 前插,命中就原地覆盖', () => {
    const first = makeRun({ id: 'a' });
    const second = makeRun({ id: 'b' });
    expect(upsertRun([first], second).map((run) => run.id)).toEqual(['b', 'a']);
    const merged = upsertRun([first], makeRun({ id: 'a', status: 'running' }));
    expect(merged).toHaveLength(1);
    expect(merged[0].status).toBe('running');
  });
});

describe('useRunsStream', () => {
  it('连上后把推送摘要并进列表缓存,状态变为 connected', async () => {
    const queryClient = new QueryClient();
    const existing = makeRun({ id: 'run-1', status: 'planning' });
    queryClient.setQueryData(queryKeys.runsPage(''), pageOf([existing]));
    connectSseMock.mockImplementation(
      async (_url: string, options: ConnectSseOptions) => {
        options.onOpen?.();
        options.onFrame?.(
          stateFrame(makeRun({ id: 'run-1', status: 'running' }), 'c1'),
        );
        options.onFrame?.(stateFrame(makeRun({ id: 'run-2' }), 'c2'));
        await new Promise<void>(() => {});
      },
    );

    const { result, unmount } = renderHook(
      () => ({ stream: useRunsStream(), status: getRunsStreamStatus() }),
      { wrapper: wrapper(queryClient) },
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(connectSseMock).toHaveBeenCalledTimes(1);
    const [url] = connectSseMock.mock.calls[0] as [string, ConnectSseOptions];
    expect(url).toBe(runsBase + '/api/v1/runs/stream');
    expect(result.current).toBeDefined();
    expect(getRunsStreamStatus().connected).toBe(true);

    const cached =
      queryClient
        .getQueryData<InfiniteData<RunPage>>(queryKeys.runsPage(''))
        ?.pages.flatMap((page) => page.items) ?? [];
    expect(cached.map((run) => run.id)).toEqual(['run-2', 'run-1']);
    expect(cached[1].status).toBe('running');

    unmount();
    expect(getRunsStreamStatus()).toEqual({
      connected: false,
      reconnecting: false,
    });
  });

  it('列表还没加载过时不凭空造数据(否则 useQuery 不再拉全量)', async () => {
    const queryClient = new QueryClient();
    connectSseMock.mockImplementation(
      async (_url: string, options: ConnectSseOptions) => {
        options.onOpen?.();
        options.onFrame?.(stateFrame(makeRun(), 'c1'));
        await new Promise<void>(() => {});
      },
    );

    renderHook(() => useRunsStream(), { wrapper: wrapper(queryClient) });
    await act(async () => {
      await Promise.resolve();
    });

    expect(queryClient.getQueryData(queryKeys.runsPage(''))).toBeUndefined();
  });

  it('断线按指数退避重连,并带上最后一帧的游标', async () => {
    vi.useFakeTimers();
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.runsPage(''), pageOf([]));
    connectSseMock
      .mockImplementationOnce(
        async (_url: string, options: ConnectSseOptions) => {
          options.onOpen?.();
          options.onFrame?.(stateFrame(makeRun(), 'c1'));
          throw new Error('连接断开');
        },
      )
      .mockImplementationOnce(
        async (_url: string, options: ConnectSseOptions) => {
          options.onOpen?.();
          await new Promise<void>(() => {});
        },
      );

    renderHook(() => useRunsStream(), { wrapper: wrapper(queryClient) });
    await act(async () => {
      await Promise.resolve();
    });
    expect(getRunsStreamStatus()).toEqual({
      connected: false,
      reconnecting: true,
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(connectSseMock).toHaveBeenCalledTimes(2);
    const [, second] = connectSseMock.mock.calls[1] as [
      string,
      ConnectSseOptions,
    ];
    expect(second.lastEventId).toBe('c1');
    expect(getRunsStreamStatus().connected).toBe(true);
  });

  it('只把新 run 前插进命中搜索词的缓存', async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(queryKeys.runsPage(''), pageOf([]));
    queryClient.setQueryData(queryKeys.runsPage('周报'), pageOf([]));
    queryClient.setQueryData(queryKeys.runsPage('登录'), pageOf([]));
    connectSseMock.mockImplementation(
      async (_url: string, options: ConnectSseOptions) => {
        options.onOpen?.();
        options.onFrame?.(
          stateFrame(makeRun({ id: 'run-9', title: '整理周报' }), 'c1'),
        );
        await new Promise<void>(() => {});
      },
    );

    renderHook(() => useRunsStream(), { wrapper: wrapper(queryClient) });
    await act(async () => {
      await Promise.resolve();
    });

    const idsOf = (q: string) =>
      queryClient
        .getQueryData<InfiniteData<RunPage>>(queryKeys.runsPage(q))
        ?.pages.flatMap((page) => page.items)
        .map((run) => run.id) ?? [];
    expect(idsOf('')).toEqual(['run-9']);
    expect(idsOf('周报')).toEqual(['run-9']);
    expect(idsOf('登录')).toEqual([]);
  });

  it('已在后续页的 run 只在该页原地更新,不重复前插到第一页', async () => {
    const queryClient = new QueryClient();
    const first = makeRun({ id: 'run-1', status: 'planning' });
    const second = makeRun({ id: 'run-2', status: 'planning' });
    queryClient.setQueryData(queryKeys.runsPage(''), {
      pages: [
        { items: [first], next_cursor: 'c1' },
        { items: [second], next_cursor: null },
      ],
      pageParams: [undefined, 'c1'],
    } satisfies InfiniteData<RunPage>);
    connectSseMock.mockImplementation(
      async (_url: string, options: ConnectSseOptions) => {
        options.onOpen?.();
        options.onFrame?.(
          stateFrame(makeRun({ id: 'run-2', status: 'running' }), 'c1'),
        );
        await new Promise<void>(() => {});
      },
    );

    renderHook(() => useRunsStream(), { wrapper: wrapper(queryClient) });
    await act(async () => {
      await Promise.resolve();
    });

    const pages = queryClient.getQueryData<InfiniteData<RunPage>>(
      queryKeys.runsPage(''),
    )?.pages;
    expect(pages?.[0].items.map((run) => run.id)).toEqual(['run-1']);
    expect(pages?.[1].items.map((run) => run.id)).toEqual(['run-2']);
    expect(pages?.[1].items[0].status).toBe('running');
  });
});
