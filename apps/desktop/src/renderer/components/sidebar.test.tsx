import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { App } from '@/app';
import {
  installFakeBridge,
  makeSession,
  removeFakeBridge,
  toSummary,
} from '@/test-utils/fake-bridge';

const SESSION = makeSession({ id: 's1', title: '周报会话' });

afterEach(() => {
  removeFakeBridge();
});

describe('侧边栏会话操作', () => {
  it('重命名会话走 sessions.rename', async () => {
    const fake = installFakeBridge({
      sessions: [toSummary(SESSION)],
      session: SESSION,
    });
    render(<App />);

    await screen.findByText('周报会话');
    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    fireEvent.click(await screen.findByRole('button', { name: '重命名' }));

    const input = await screen.findByLabelText('会话标题');
    fireEvent.change(input, { target: { value: '新的标题' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(fake.mocks.sessionsRename).toHaveBeenCalledWith('s1', '新的标题'),
    );
  });

  it('删除需要二次确认,确认后走 sessions.remove', async () => {
    const fake = installFakeBridge({
      sessions: [toSummary(SESSION)],
      session: SESSION,
    });
    render(<App />);

    await screen.findByText('周报会话');
    fireEvent.click(screen.getByRole('button', { name: '会话操作' }));
    fireEvent.click(await screen.findByRole('button', { name: '删除' }));

    expect(await screen.findByText('删除后不可恢复,确定?')).toBeInTheDocument();
    expect(fake.mocks.sessionsRemove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await waitFor(() =>
      expect(fake.mocks.sessionsRemove).toHaveBeenCalledWith('s1'),
    );
  });
});
