import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import {
  connectorProviderFromToolName,
  connectorProviderLabel,
  ConnectorRecoveryCard,
} from './connector-recovery-card';

function renderCard(
  errorCode: string | undefined,
  toolName = 'Demo__read_document',
) {
  return render(
    <MemoryRouter>
      <ConnectorRecoveryCard errorCode={errorCode} toolName={toolName} />
    </MemoryRouter>,
  );
}

describe('ConnectorRecoveryCard', () => {
  it('connection_missing:提示未连接并给出带 provider 的连接器入口', () => {
    renderCard('connector.connection_missing');

    expect(screen.getByText('尚未连接Demo')).toBeTruthy();
    const link = screen.getByRole('link', { name: '去连接器' });
    expect(link.getAttribute('href')).toBe('/connectors?provider=demo');
  });

  it('auth_expired:提示连接失效且不承诺自动续跑', () => {
    renderCard('connector.auth_expired');

    expect(screen.getByText('Demo 连接已失效')).toBeTruthy();
    expect(screen.getByText(/本次任务不会自动继续/)).toBeTruthy();
  });

  it('scope_missing:提示补 scope 后重新授权', () => {
    renderCard('connector.scope_missing');

    expect(screen.getByText('Demo 授权缺少权限')).toBeTruthy();
    expect(screen.getByText(/勾选对应权限/)).toBeTruthy();
  });

  it('非连接器错误码不渲染恢复卡', () => {
    renderCard('tool.confirmation_required');

    expect(screen.queryByRole('note')).toBeNull();
    expect(document.body.textContent).toBe('');
  });

  it('工具名没有 provider 前缀时回落到通用连接器页', () => {
    renderCard('connector.connection_missing', 'read_file');

    expect(screen.getByText('尚未连接连接器')).toBeTruthy();
    const link = screen.getByRole('link', { name: '去连接器' });
    expect(link.getAttribute('href')).toBe('/connectors');
  });

  it('provider 前缀小写化,展示名按首字母大写', () => {
    expect(connectorProviderFromToolName('Demo__read_document')).toBe('demo');
    expect(connectorProviderFromToolName('read_file')).toBeNull();
    expect(connectorProviderLabel('demo')).toBe('Demo');
    expect(connectorProviderLabel(null)).toBe('连接器');
  });
});
