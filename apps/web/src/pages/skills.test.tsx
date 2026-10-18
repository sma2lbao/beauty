import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { skillsApi } from '@/lib/skills-api';

import { SkillsPage } from './skills';

vi.mock('@/lib/skills-api', () => ({
  skillsApi: { list: vi.fn(), get: vi.fn() },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SkillsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SkillsPage', () => {
  it('展示技能与工具白名单', async () => {
    vi.mocked(skillsApi.list).mockResolvedValue([
      {
        id: 'office.email-summary',
        version: '1.0.0',
        title: '邮件摘要',
        description: '提炼关键信息',
        workspace_type: 'mailbox',
        tools: ['list_connections', 'fetch_emails'],
        permissions: {},
        system_prompt: 'x',
        input_schema: {},
        deliverables: [{ kind: 'markdown' }],
        triggers: ['manual'],
      },
    ]);
    renderPage();
    expect(await screen.findByText('邮件摘要')).toBeInTheDocument();
    expect(screen.getByText('fetch_emails')).toBeInTheDocument();
    expect(screen.getByText('用此技能开始')).toBeInTheDocument();
  });
});
