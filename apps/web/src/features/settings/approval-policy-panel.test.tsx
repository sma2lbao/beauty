import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { approvalApi, type ApprovalPolicy } from '@/lib/approval-api';

import { ApprovalPolicyPanel } from './approval-policy-panel';

vi.mock('@/lib/approval-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/approval-api')>(
      '@/lib/approval-api',
    );
  return {
    ...actual,
    approvalApi: { list: vi.fn(), replace: vi.fn() },
  };
});

const toolLevel: ApprovalPolicy = {
  tool: 'exec',
  action: 'confirm',
  arg_name: '',
  arg_match: '',
  arg_value: '',
};

const argLevel: ApprovalPolicy = {
  tool: '*',
  action: 'auto',
  arg_name: 'command',
  arg_match: 'prefix',
  arg_value: 'git status',
};

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ApprovalPolicyPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(approvalApi.list).mockResolvedValue([]);
});

describe('ApprovalPolicyPanel', () => {
  it('渲染已有规则:工具、参数条件、动作与删除按钮', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel, argLevel]);
    renderPanel();

    expect(await screen.findByText('exec')).toBeTruthy();
    expect(screen.getByText('command prefix git status')).toBeTruthy();
    const table = screen.getByRole('table');
    expect(within(table).getByText('需确认')).toBeTruthy();
    expect(within(table).getByText('自动执行')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '删除 exec 的规则' }),
    ).toBeTruthy();
  });

  it('空表说明默认风险语义', async () => {
    renderPanel();

    expect(
      await screen.findByText(/还没有规则:默认按风险等级处理/),
    ).toBeTruthy();
  });

  it('新增规则后保存:整表 PUT 并重取列表', async () => {
    vi.mocked(approvalApi.replace).mockResolvedValue([toolLevel]);
    renderPanel();
    await screen.findByText(/还没有规则/);

    fireEvent.change(screen.getByLabelText('工具名'), {
      target: { value: 'exec' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }));
    expect(screen.getByText('exec')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    await waitFor(() => {
      expect(approvalApi.replace).toHaveBeenCalledWith([
        { tool: 'exec', action: 'confirm' },
      ]);
    });
    await waitFor(() => {
      expect(approvalApi.list).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText('已保存')).toBeTruthy();
  });

  it('新增参数级规则时保存参数三列', async () => {
    vi.mocked(approvalApi.replace).mockResolvedValue([argLevel]);
    renderPanel();
    await screen.findByText(/还没有规则/);

    fireEvent.change(screen.getByLabelText('工具名'), {
      target: { value: '*' },
    });
    fireEvent.change(screen.getByLabelText('参数名(可选)'), {
      target: { value: 'command' },
    });
    fireEvent.change(screen.getByLabelText('匹配方式'), {
      target: { value: 'prefix' },
    });
    fireEvent.change(screen.getByLabelText('参数值'), {
      target: { value: 'git status' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }));
    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    await waitFor(() => {
      expect(approvalApi.replace).toHaveBeenCalledWith([
        {
          tool: '*',
          action: 'confirm',
          arg_name: 'command',
          arg_match: 'prefix',
          arg_value: 'git status',
        },
      ]);
    });
  });

  it('删除已有行后保存:整表 PUT 不含该行', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel]);
    vi.mocked(approvalApi.replace).mockResolvedValue([]);
    renderPanel();
    await screen.findByText('exec');

    fireEvent.click(screen.getByRole('button', { name: '删除 exec 的规则' }));
    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    await waitFor(() => {
      expect(approvalApi.replace).toHaveBeenCalledWith([]);
    });
  });

  it('保存失败时展示错误,不静默', async () => {
    vi.mocked(approvalApi.replace).mockRejectedValue(new Error('服务不可用'));
    renderPanel();
    await screen.findByText(/还没有规则/);

    fireEvent.change(screen.getByLabelText('工具名'), {
      target: { value: 'exec' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }));
    fireEvent.click(screen.getByRole('button', { name: '保存策略' }));

    expect(await screen.findByText('保存失败:服务不可用')).toBeTruthy();
  });

  it('重复规则在本地拦截,不发请求', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel]);
    renderPanel();
    await screen.findByText('exec');

    fireEvent.change(screen.getByLabelText('工具名'), {
      target: { value: 'exec' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }));

    expect(
      await screen.findByText('规则重复:同一工具与参数条件只能有一条。'),
    ).toBeTruthy();
    expect(approvalApi.replace).not.toHaveBeenCalled();
  });
});
