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
  /** 过程项投影(P1-D 事件分类学):['run-items', runId]。 */
  runItems: (runId: string) => ['run-items', runId] as const,
  /** 任务清单投影:['run-todos', runId]。 */
  runTodos: (runId: string) => ['run-todos', runId] as const,
  /** 子代理列表(P1-E):['run-subagents', runId]。 */
  runSubagents: (runId: string) => ['run-subagents', runId] as const,
  /** run 用量汇总(P1-E):['run-usage', runId]。 */
  runUsage: (runId: string) => ['run-usage', runId] as const,
  /** run 评分(P2 eval/scores):['run-scores', runId]。 */
  runScores: (runId: string) => ['run-scores', runId] as const,
  runArtifacts: (runId: string) => ['run-artifacts', runId] as const,
  /** 图检查点列表(P1-C):['run-checkpoints', runId];rewind/fork 后失效。 */
  runCheckpoints: (runId: string) => ['run-checkpoints', runId] as const,
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

  /** 账号域:当前账号资料与设备会话。 */
  authAccount: ['auth-account'] as const,
  accountMe: ['account-me'] as const,
  accountSessions: ['account-sessions'] as const,

  /** 工具审批策略域(P1-A):整表读/写共用同一键,PUT 成功后失效重取。 */
  approvalPolicies: ['approval-policies'] as const,

  /** 自动化 hooks 域(P1-B):列表键,创建/更新/删除/轮换后统一失效重取。 */
  hooks: ['hooks'] as const,

  /**
   * 管理台域(P0-7):列表键把过滤维度拼进键(维度变→自动重查);
   * adminAccounts / adminAuditLogs 是域级前缀键,写操作后统一失效。
   */
  adminAccounts: ['admin-accounts'] as const,
  adminAccountList: (
    query: string,
    status: string,
    limit: number,
    offset: number,
  ) => ['admin-accounts', 'list', query, status, limit, offset] as const,
  adminAuditLogs: ['admin-audit-logs'] as const,
  adminAuditLogList: (
    limit: number,
    offset: number,
    actorAccountId: string,
    action: string,
    targetId: string,
  ) =>
    [
      'admin-audit-logs',
      'list',
      limit,
      offset,
      actorAccountId,
      action,
      targetId,
    ] as const,

  /** 模型与用量域。 */
  models: ['models'] as const,
  /**
   * 对话可选模型目录(P1-G):`GET /api/v1/models` 的投影。
   * 与 legacy `models`(insightsApi 的 `/api/models`,响应形状不同)分开建键,
   * 避免两条 queryFn 共用同一缓存键互相覆盖。
   */
  chatModels: ['chat-models'] as const,
  usageSummary: ['usage-summary'] as const,
  usageQuota: ['usage-quota'] as const,
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

  /** 连接器域:provider 目录 / 连接列表 / 单连接详情。 */
  connectorProviders: ['connector-providers'] as const,
  connectorConnections: ['connector-connections'] as const,
  connectorConnection: (id: string | null | undefined) =>
    ['connector-connection', id] as const,

  /** MCP / Skills 域。 */
  mcpServers: ['mcp-servers'] as const,
  /** MCP 预设目录(连接器页的一键接入清单)。 */
  mcpPresets: ['mcp-presets'] as const,
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
