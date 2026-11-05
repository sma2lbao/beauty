/**
 * MCP 预设卡片:四态渲染(纯 props,不发请求)。
 *
 * 卡片是 /connectors 预设区的最小单元,状态判定在 mcp-presets.ts,
 * 这里只钉住每种状态下用户看到的文案与可达链接。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/http';
import type { MCPServer } from '@/lib/mcp-api';
import { describeOAuthStartError } from '@/features/mcp/oauth-start-error';
import type { OAuthStartErrorInfo } from '@/features/mcp/oauth-start-error';

import { McpPresetCard } from './mcp-preset-card';
import type { McpPreset, McpPresetCardState } from './mcp-presets';

const notionPreset: McpPreset = {
  key: 'notion',
  name: 'Notion',
  url: 'https://mcp.notion.com/mcp',
  description: '搜索与读取页面/数据库、更新内容、查看日历。',
  docs_url: 'https://developers.notion.com/docs/mcp',
};

function makeServer(overrides: Partial<MCPServer> = {}): MCPServer {
  return {
    id: 'srv-1',
    name: 'Notion',
    transport: 'http',
    url: 'https://mcp.notion.com/mcp',
    enabled: true,
    auto_approve: false,
    has_token: false,
    token_hint: null,
    command: null,
    args: [],
    runtime: 'python',
    image: null,
    has_env: false,
    env_keys: [],
    tools_allowlist: [],
    tools: [],
    timeout_seconds: 30,
    last_status: 'ok',
    last_checked_at: null,
    last_error: null,
    has_oauth: true,
    oauth_authorized: false,
    oauth_scopes: [],
    oauth_expires_at: null,
    oauth_authorization_server: null,
    oauth_error: null,
    created_at: '2026-11-04T08:00:00+00:00',
    ...overrides,
  };
}

function renderCard(
  state: McpPresetCardState,
  options: {
    server?: MCPServer | null;
    connecting?: boolean;
    actionError?: OAuthStartErrorInfo | null;
    onConnect?: () => void;
    onUseToken?: () => void;
  } = {},
) {
  const onConnect = options.onConnect ?? vi.fn();
  render(
    <MemoryRouter>
      <McpPresetCard
        preset={notionPreset}
        server={options.server ?? null}
        state={state}
        connecting={options.connecting ?? false}
        actionError={options.actionError ?? null}
        onConnect={onConnect}
        onUseToken={options.onUseToken}
      />
    </MemoryRouter>,
  );
  return { onConnect };
}

describe('McpPresetCard', () => {
  it('未连接:展示预设说明与「连接」按钮', () => {
    const { onConnect } = renderCard('not_linked');

    expect(screen.getByText('Notion')).toBeTruthy();
    expect(screen.getByText('未连接')).toBeTruthy();
    expect(screen.getByText(/还没有创建连接/)).toBeTruthy();
    const button = screen.getByRole('button', { name: '连接 Notion' });
    button.click();
    expect(onConnect).toHaveBeenCalledTimes(1);
    // 未连接时没有「去会话使用」入口
    expect(screen.queryByRole('link', { name: /去会话使用/ })).toBeNull();
  });

  it('待授权且上次失败:展示失败原因与「重新授权」按钮', () => {
    renderCard('needs_auth', {
      server: makeServer({
        oauth_authorized: false,
        oauth_error: 'invalid_client',
        last_status: 'auth_required',
      }),
    });

    expect(screen.getByText('授权失败')).toBeTruthy();
    expect(screen.getByText(/上次授权没有成功：invalid_client/)).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '重新授权 Notion' }),
    ).toBeTruthy();
  });

  it('已连接:展示工具数、「去会话使用」与「管理」链接', () => {
    renderCard('connected', {
      server: makeServer({
        oauth_authorized: true,
        tools: [
          { name: 'notion_search', description: '搜索', input_schema: {} },
          { name: 'notion_read', description: '读取', input_schema: {} },
        ],
      }),
    });

    expect(screen.getByText('已连接')).toBeTruthy();
    expect(screen.getByText('已连接 · 2 个工具')).toBeTruthy();
    const open = screen.getByRole('link', { name: '去会话使用 Notion' });
    expect(open.getAttribute('href')).toBe('/?mcp=srv-1');
    expect(open.textContent).toContain('去会话使用');
    const manage = screen.getByRole('link', { name: '管理 Notion' });
    // MCP 管理区已并入连接器页,「管理」直接落到本页的 #mcp-servers
    expect(manage.getAttribute('href')).toBe('/connectors#mcp-servers');
    expect(screen.queryByRole('button', { name: /连接 Notion/ })).toBeNull();
  });

  it('已停用:提示到 MCP 页启用并给管理入口', () => {
    renderCard('disabled', {
      server: makeServer({ enabled: false, oauth_authorized: true }),
    });

    expect(screen.getByText('已停用')).toBeTruthy();
    expect(screen.getByText(/已在「我的 MCP 服务」停用/)).toBeTruthy();
    const manage = screen.getByRole('link', { name: '管理 Notion' });
    expect(manage.getAttribute('href')).toBe('/connectors#mcp-servers');
    expect(manage.textContent).toContain('到「我的 MCP 服务」启用');
    expect(screen.queryByRole('link', { name: /去会话使用/ })).toBeNull();
  });

  it('发起授权失败:错误只落在这张卡片上,按钮可再试', () => {
    renderCard('not_linked', {
      actionError: describeOAuthStartError(
        new Error('预设创建失败，请稍后重试。'),
      ),
    });

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('预设创建失败');
    expect(screen.getByRole('button', { name: '连接 Notion' })).toBeTruthy();
  });

  it('分类失败面板:token 类给出「改用令牌」并回调', () => {
    const onUseToken = vi.fn();
    renderCard('needs_auth', {
      server: makeServer(),
      actionError: describeOAuthStartError(
        new ApiError(502, 'mcp.oauth_discovery_failed', '只支持静态令牌'),
      ),
      onUseToken,
    });

    expect(screen.getByText('该 server 未启用 OAuth 授权')).toBeTruthy();
    expect(screen.getByText('错误码 mcp.oauth_discovery_failed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '改用令牌' }));
    expect(onUseToken).toHaveBeenCalledTimes(1);
  });

  it('分类失败面板:没有 onUseToken 时不渲染「改用令牌」', () => {
    renderCard('needs_auth', {
      server: makeServer(),
      actionError: describeOAuthStartError(
        new ApiError(502, 'mcp.oauth_discovery_failed', '只支持静态令牌'),
      ),
    });

    expect(screen.queryByRole('button', { name: '改用令牌' })).toBeNull();
  });

  it('跳转中:按钮禁用并显示加载文案', () => {
    renderCard('not_linked', { connecting: true });

    const button = screen.getByRole('button', {
      name: '连接 Notion',
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain('跳转中…');
  });

  it('有官方文档时给外链', () => {
    renderCard('not_linked');

    const docs = screen.getByRole('link', { name: /官方文档/ });
    expect(docs.getAttribute('href')).toBe(
      'https://developers.notion.com/docs/mcp',
    );
  });
});
