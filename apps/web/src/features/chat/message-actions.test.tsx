import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentBlock, UserBlock } from './message-bubbles';

const USER_TEXT = '把本周日程整理成周报';

function stubClipboard(writeText: (text: string) => Promise<void>) {
  vi.stubGlobal('navigator', { clipboard: { writeText } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('UserBlock 消息操作行(interaction-closure §2.2)', () => {
  it('复制成功后就地显示「已复制」并写入剪贴板', async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard(writeText);
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{ busy: false, error: null, onResend: vi.fn() }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制' }));

    expect(await screen.findByText('已复制')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(USER_TEXT);
  });

  it('剪贴板失败时就地提示「复制失败」而不是抛错', async () => {
    stubClipboard(async () => Promise.reject(new Error('denied')));
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{ busy: false, error: null, onResend: vi.fn() }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '复制' }));

    expect(await screen.findByText('复制失败')).toBeInTheDocument();
  });

  it('编辑重发:textarea 预填原文,空内容拒绝提交,有效内容回调 onResend', async () => {
    stubClipboard(async () => {});
    const onResend = vi.fn();
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{ busy: false, error: null, onResend }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '编辑重发' }));
    const textarea = screen.getByLabelText('编辑消息内容');
    expect(textarea).toHaveValue(USER_TEXT);

    fireEvent.change(textarea, { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '确认重发' }));

    expect(screen.getByRole('alert')).toHaveTextContent('内容长度需为 1–2000 字。');
    expect(onResend).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: '改过的内容' } });
    fireEvent.click(screen.getByRole('button', { name: '确认重发' }));

    expect(onResend).toHaveBeenCalledWith('改过的内容');
    expect(screen.queryByLabelText('编辑消息内容')).toBeNull();
  });

  it('取消编辑不触发重发,恢复原文展示', () => {
    stubClipboard(async () => {});
    const onResend = vi.fn();
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{ busy: false, error: null, onResend }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '编辑重发' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(screen.queryByLabelText('编辑消息内容')).toBeNull();
    expect(screen.getByText(USER_TEXT)).toBeInTheDocument();
    expect(onResend).not.toHaveBeenCalled();
  });

  it('流进行中(busy)禁用复制与编辑重发', () => {
    stubClipboard(async () => {});
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{ busy: true, error: null, onResend: vi.fn() }}
      />,
    );

    expect(screen.getByRole('button', { name: '复制' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '编辑重发' })).toBeDisabled();
  });

  it('错误文案就地展示在气泡里', () => {
    render(
      <UserBlock
        text={USER_TEXT}
        actions={{
          busy: false,
          error: '只能重写最后一轮消息,请刷新后再试。',
          onResend: vi.fn(),
        }}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      '只能重写最后一轮消息,请刷新后再试。',
    );
  });
});

describe('AgentBlock 重新生成', () => {
  it('点「重新生成」回调 onRegenerate,并支持复制正文', async () => {
    const writeText = vi.fn(async () => {});
    stubClipboard(writeText);
    const onRegenerate = vi.fn();
    render(
      <AgentBlock
        meta="beauty moon Agent"
        actions={{ busy: false, error: null, onRegenerate }}
      >
        这是回复
      </AgentBlock>,
    );

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '复制' }));
    expect(await screen.findByText('已复制')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith('这是回复');
  });

  it('流进行中禁用重新生成', () => {
    render(
      <AgentBlock
        meta="beauty moon Agent"
        actions={{ busy: true, error: null, onRegenerate: vi.fn() }}
      >
        这是回复
      </AgentBlock>,
    );

    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled();
  });
});
