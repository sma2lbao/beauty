/**
 * react-query 缓存键唯一真源(R-09):components/pages 此前散落的 queryKey
 * 数组字面量收敛到本工厂。**形状与顺序和收敛前逐处一致**(react-query
 * 按数组序列做哈希,形状变了缓存与失效行为就变),替换时只换写法、不改值。
 *
 * 分组约定:域内细粒度键(如 keys.run.detail(id))供 useQuery/单键失效;
 * 域级前缀键(如 keys.runs)供 invalidateQueries 前缀匹配(命中全部
 * 以该前缀开头的键,与原 `['runs']` 字面量行为等价)。
 */

export const queryKeys = {
  /** 运行域:['runs'] 列表 / ['run', id] 详情。 */
  runs: ['runs'] as const,
  run: (runId: string) => ['run', runId] as const,
  /** 执行计划:['plan', runId]。 */
  plan: (runId: string) => ['plan', runId] as const,
  /** 追加指令:['instructions', runId]。 */
  instructions: (runId: string) => ['instructions', runId] as const,
  /** 执行步骤投影:['run-steps', runId]。 */
  runSteps: (runId: string) => ['run-steps', runId] as const,
  runArtifacts: (runId: string) => ['run-artifacts', runId] as const,
  runArtifactPreview: (runId: string, previewId: string | null | undefined) =>
    ['run-artifact-preview', runId, previewId] as const,

  /**
   * 会话域。
   * - conversations:['conversations'] 列表前缀(invalidateQueries 前缀匹配命中
   *   ['conversations', ...] 全部变体;useQuery 可在其后追加过滤维度,如
   *   ['conversations', 'all', showArchived, query] / ['conversations', workspaceId]);
   * - conversationPrefix:['conversation'] 详情前缀(命中 ['conversation', id]);
   * - conversation:单条会话详情键 ['conversation', id]。
   */
  conversations: ['conversations'] as const,
  conversationPrefix: ['conversation'] as const,
  conversation: (id: string) => ['conversation', id] as const,
  conversationMessages: (conversationId: string) =>
    ['conversation-messages', conversationId] as const,
  conversationAttachments: (conversationId: string) =>
    ['conversation-attachments', conversationId] as const,

  /**
   * 会话沙箱域(执行现场的「文件 / 终端」页签):
   * - conversationSandbox:['conversation-sandbox', conversationId] 会话对应沙箱;
   * - sandboxTree:['sandbox-tree', sandboxId, path, depth] 目录列表;
   * - sandboxFile:['sandbox-file', sandboxId, path] 文件内容;
   * - conversationToolCalls:['conversation-tool-calls', conversationId]
   *   会话级工具调用审计(服务端补丁与增删行数的来源)。
   */
  conversationSandbox: (conversationId: string) =>
    ['conversation-sandbox', conversationId] as const,
  conversationToolCalls: (conversationId: string) =>
    ['conversation-tool-calls', conversationId] as const,
  sandboxTree: (sandboxId: string, path: string, depth: number) =>
    ['sandbox-tree', sandboxId, path, depth] as const,
  sandboxFile: (sandboxId: string, path: string) =>
    ['sandbox-file', sandboxId, path] as const,

  /** 工作区域。 */
  workspaces: ['workspaces'] as const,
  defaultWorkspace: ['workspace', 'default'] as const,
  /** ['workspace-artifacts'] 前缀:invalidateQueries 命中各工作区维度的 ['workspace-artifacts', wsId]。 */
  workspaceArtifactsAll: ['workspace-artifacts'] as const,
  workspaceArtifacts: (workspaceId: string | null | undefined) =>
    ['workspace-artifacts', workspaceId] as const,
  workspaceArtifact: (artifactId: string | null | undefined) =>
    ['workspace-artifact', artifactId] as const,
  workspaceArtifactBlob: (artifactId: string | null | undefined) =>
    ['workspace-artifact-blob', artifactId] as const,
  artifacts: ['artifacts'] as const,

  /** 账号/组织域。 */
  authAccount: ['auth-account'] as const,
  organization: ['organization'] as const,
  accountOrganization: ['account-organization'] as const,
  accountMembers: ['account-members'] as const,
  accountSessions: ['account-sessions'] as const,

  /** 模型与用量域。 */
  models: ['models'] as const,
  usageSummary: ['usage-summary'] as const,
  apiKeys: ['api-keys'] as const,

  /** 知识库域。 */
  knowledgeDocuments: ['knowledge-documents'] as const,

  /**
   * 记忆域:memories 是域级前缀键(命中列表/统计/检索),写操作后统一失效;
   * 列表与检索把过滤维度拼进键,维度变化自然重查。
   */
  memories: ['memories'] as const,
  memoryList: (kind?: string, workspaceId?: string) =>
    ['memories', 'list', kind ?? '', workspaceId ?? ''] as const,
  memoryStats: ['memories', 'stats'] as const,
  memorySearch: (query: string, kind?: string, workspaceId?: string) =>
    ['memories', 'search', query, kind ?? '', workspaceId ?? ''] as const,

  /** MCP / Skills 域。 */
  mcpServers: ['mcp-servers'] as const,
  skills: ['skills'] as const,
  skillPackages: ['skill-packages'] as const,
  oauthClient: (clientId: string) => ['oauth-client', clientId] as const,
  oauthAuthorizations: ['oauth-authorizations'] as const,
  byok: ['byok'] as const,

  /** outbox 事件队列域:['outbox-events', status?] / ['outbox-stats']。 */
  outboxEvents: (status?: string) =>
    status === undefined
      ? (['outbox-events'] as const)
      : (['outbox-events', status] as const),
  outboxStats: ['outbox-stats'] as const,
} as const;
