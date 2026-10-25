import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { skillsApi } from '@/lib/skills-api';
import type { SkillPackage } from '@/lib/skills-api';

import { SkillsPage } from './skills';

vi.mock('@/lib/skills-api', () => ({
  skillsApi: {
    list: vi.fn(),
    get: vi.fn(),
    listPackages: vi.fn(),
    getPackage: vi.fn(),
    uploadPackage: vi.fn(),
    deletePackage: vi.fn(),
  },
}));

const PACKAGE: SkillPackage = {
  id: 'p1',
  slug: 'demo-skill',
  title: '演示技能包',
  description: '从 zip 上传的技能包',
  latest_version: '1.2.0',
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-10-20T08:00:00+00:00',
  versions: [
    {
      id: 'v2',
      version: '1.2.0',
      size_bytes: 12_800,
      sha256: 'a'.repeat(64),
      entrypoint: ['SKILL.md', 'scripts/run.py'],
      runtime: 'docker',
      network: 'none',
      timeout_seconds: 120,
      has_scripts: true,
      created_at: '2026-10-20T08:00:00+00:00',
    },
    {
      id: 'v1',
      version: '1.0.0',
      size_bytes: 10_240,
      sha256: 'b'.repeat(64),
      entrypoint: ['SKILL.md'],
      runtime: 'docker',
      network: 'none',
      timeout_seconds: 120,
      has_scripts: false,
      created_at: '2026-10-01T08:00:00+00:00',
    },
  ],
};

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

function pickFile(name = 'demo.zip') {
  const input = document.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  const file = new File(['zip'], name, { type: 'application/zip' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(skillsApi.list).mockResolvedValue([]);
  vi.mocked(skillsApi.listPackages).mockResolvedValue([]);
});

describe('SkillsPage', () => {
  it('展示技能与工具白名单,并标出来源与含脚本', async () => {
    vi.mocked(skillsApi.list).mockResolvedValue([
      {
        id: 'coding.repo-readonly-review',
        version: '1.0.0',
        title: '只读代码审查',
        description: '提炼关键信息',
        workspace_type: 'local',
        tools: ['glob', 'read', 'grep'],
        permissions: {},
        system_prompt: 'x',
        input_schema: {},
        deliverables: [{ kind: 'markdown' }],
        triggers: ['manual'],
        source: 'builtin',
        has_scripts: false,
      },
      {
        id: 'demo-skill',
        version: '1.2.0',
        title: '演示技能',
        description: '来自上传包',
        workspace_type: 'local',
        tools: [],
        permissions: {},
        system_prompt: 'x',
        input_schema: {},
        deliverables: [{ kind: 'markdown' }],
        triggers: ['manual'],
        source: 'uploaded',
        has_scripts: true,
        package_id: 'p1',
        version_id: 'v2',
      },
    ]);
    renderPage();

    expect(await screen.findByText('只读代码审查')).toBeInTheDocument();
    expect(screen.getByText('glob')).toBeInTheDocument();
    expect(screen.getByText('read')).toBeInTheDocument();
    expect(screen.getByText('grep')).toBeInTheDocument();
    expect(screen.getAllByText('用此技能开始')).toHaveLength(2);
    expect(screen.getByText('内置')).toBeInTheDocument();
    expect(screen.getByText('上传')).toBeInTheDocument();
    expect(screen.getByText('含脚本')).toBeInTheDocument();
  });

  it('上传失败时展示后端 message', async () => {
    vi.mocked(skillsApi.uploadPackage).mockRejectedValue(
      new Error('技能包缺少 SKILL.md'),
    );
    renderPage();

    const file = pickFile();
    expect(screen.getByText('demo.zip')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '技能包缺少 SKILL.md',
    );
    expect(skillsApi.uploadPackage).toHaveBeenCalledWith(file);
  });

  it('上传成功后刷新技能与包列表', async () => {
    vi.mocked(skillsApi.uploadPackage).mockResolvedValue(PACKAGE);
    renderPage();
    await waitFor(() => expect(skillsApi.list).toHaveBeenCalledTimes(1));

    pickFile();
    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));

    expect(await screen.findByText(/已上传/)).toBeInTheDocument();
    // invalidate 两个 query:各再请求一次
    await waitFor(() => expect(skillsApi.list).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(skillsApi.listPackages).toHaveBeenCalledTimes(2),
    );
    // 选择文件被清空
    expect(screen.getByText('未选择文件')).toBeInTheDocument();
  });

  it('我的上传:展示版本信息,可展开并删除(需确认)', async () => {
    vi.mocked(skillsApi.listPackages).mockResolvedValue([PACKAGE]);
    renderPage();

    expect(await screen.findByText('演示技能包')).toBeInTheDocument();
    expect(screen.getByText('v1.2.0')).toBeInTheDocument();
    expect(screen.getByText('2 个版本')).toBeInTheDocument();
    expect(screen.getByText('12.5 KB')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '版本(2)' }));
    expect(
      screen.getByText(/entrypoint SKILL\.md, scripts\/run\.py/),
    ).toBeInTheDocument();
    // 两个版本都展示运行时信息
    expect(screen.getAllByText(/runtime docker/)).toHaveLength(2);

    vi.mocked(skillsApi.deletePackage).mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    expect(screen.getByText('删除技能包')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await waitFor(() =>
      expect(skillsApi.deletePackage).toHaveBeenCalledWith('p1'),
    );
  });
});
