/**
 * 会话页的数据层:agent-orchestrator 的 conversation API 契约。
 *
 * 与 runs-api 同一服务(BASE 同源,Vite dev 由 vite.config.ts 代理到 :8002);
 * 会话语义:一条用户消息先由服务端**分流**——简单会话直接回答(reply,不建 run),
 * 复杂任务才建 run(自动规划)或并入当前 run 的追加指令队列;
 * postMessage 的 mode 告诉页面实际发生了哪种。
 */

import { authHeaders } from '@/lib/auth';
import { ApiError, createHttpClient } from '@/lib/http';
import { connectSse, parseSseFrameText } from '@/lib/sse';
import type { Run } from '@/lib/runs-api';
import { toolCallMetaFrom, type ToolCallMeta } from '@/lib/tool-meta';
import { runsBase as BASE } from '@/config/env';

export type Conversation = {
  id: string;
  title: string;
  /**
   * 会话归属的工作区;旧后端/旧数据可能缺省(null/未返回)。
   * 列表与详情都由服务端按 workspace_id 过滤或回填。
   */
  workspace_id?: string | null;
  created_at: string;
  updated_at: string;
  /** 仅详情返回:当前进行中的 run(接 SSE 用);列表项不带该字段 */
  active_run_id?: string | null;
  /** 归档时间;null/未返回表示未归档 */
  archived_at?: string | null;
  /**
   * 会话默认模型(C1 模型路由 §6.3):null/未返回表示不指定,由网关默认路由。
   * 单条消息还能用 `MessageCreate.model` 临时覆盖(优先级更高)。
   */
  model?: string | null;
};

export type ConversationPage = {
  items: Conversation[];
  next_cursor: string | null;
};

export type ConversationListParams = {
  limit?: number;
  /** 只看该工作区的会话;不传由服务端按默认工作区解析 */
  workspace_id?: string;
  /** 标题搜索(服务端模糊匹配) */
  q?: string;
  /** 只看已归档 */
  archived?: boolean;
  /** 上一页返回的 next_cursor */
  cursor?: string;
};

export type ChatMessage = {
  id: string;
  conversation_id: string;
  run_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  /**
   * 这条消息显式引用的 MCP server id(后端保证总是数组,旧数据可能缺省)。
   * 空数组 = 未指定,沿用全部启用 server 的现状。
   */
  mcp_server_ids?: string[];
};

/** 发送方式:auto = 服务端自动分流(默认);chat = 直接回答;task = 作为任务。 */
export type SendMode = 'auto' | 'chat' | 'task';

/** 会话附件元数据;正文走 downloadAttachment(不塞进消息 JSON)。 */
export type Attachment = {
  id: string;
  conversation_id: string;
  message_id: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
};

/**
 * 发消息的结果:
 * - chat:reply 是直接回复,run 为空(没建任务);
 * - run_created:run 是新任务;
 * - instruction_queued:run 是并入的进行中任务。
 */
export type MessagePostResult = {
  message: ChatMessage;
  run: Run | null;
  reply: ChatMessage | null;
  mode: 'chat' | 'run_created' | 'instruction_queued';
};

const http = createHttpClient(BASE);

function listPath(params: ConversationListParams = {}): string {
  const search = new URLSearchParams();
  search.set('limit', String(params.limit ?? 50));
  if (params.workspace_id) search.set('workspace_id', params.workspace_id);
  const q = params.q?.trim();
  if (q) search.set('q', q);
  if (params.archived) search.set('archived', 'true');
  if (params.cursor) search.set('cursor', params.cursor);
  return `/api/v1/conversations?${search.toString()}`;
}

export const conversationsApi = {
  /**
   * 分页列表(侧边栏"加载更多"用)。
   *
   * 兼容滚动升级:旧版后端返回 Conversation[](没有 items/next_cursor),
   * 这里统一归一化,避免前后端短暂版本错配时整个侧边栏崩溃。
   */
  page: async (
    params: ConversationListParams = {},
  ): Promise<ConversationPage> => {
    const data = await http.request<ConversationPage | Conversation[]>(
      listPath(params),
    );
    return Array.isArray(data) ? { items: data, next_cursor: null } : data;
  },
  /** 兼容旧调用:只取当前页 items */
  list: async (params: ConversationListParams = {}) =>
    (await conversationsApi.page(params)).items,
  update: (
    conversationId: string,
    patch: { title?: string; archived?: boolean; model?: string | null },
  ) =>
    http.request<Conversation>(`/api/v1/conversations/${conversationId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  remove: (conversationId: string) =>
    http.request<void>(`/api/v1/conversations/${conversationId}`, {
      method: 'DELETE',
    }),
  /**
   * 建会话:workspace_id 决定会话归属(不传由服务端落租户默认工作区);
   * title 留空则等首条消息自动定题。空调用仍发 `{}`,保持旧契约。
   * 新会话在输入区选的模型随建会话一并落库(§6.3 `ConversationCreate.model`)。
   */
  create: (
    input: {
      title?: string;
      workspace_id?: string | null;
      model?: string | null;
    } = {},
  ) => {
    const body: { title?: string; workspace_id?: string; model?: string } = {};
    const title = input.title?.trim();
    if (title) body.title = title;
    if (input.workspace_id) body.workspace_id = input.workspace_id;
    // 只在显式选了模型时才带;默认模型不落键,保持旧请求体形状
    if (input.model) body.model = input.model;
    return http.request<Conversation>('/api/v1/conversations', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  get: (conversationId: string) =>
    http.request<Conversation>(`/api/v1/conversations/${conversationId}`),
  messages: (conversationId: string) =>
    http.request<ChatMessage[]>(
      `/api/v1/conversations/${conversationId}/messages`,
    ),
  postMessage: (
    conversationId: string,
    content: string,
    mode: SendMode = 'auto',
    attachmentIds: string[] = [],
    skillId?: string,
    mcpServerIds: string[] = [],
    model?: string,
  ) =>
    http.request<MessagePostResult>(
      `/api/v1/conversations/${conversationId}/messages`,
      {
        method: 'POST',
        body: JSON.stringify({
          content,
          mode,
          attachment_ids: attachmentIds,
          skill_id: skillId,
          // 空数组不落进 body:后端默认就是 [],旧契约测试也保持原样
          mcp_server_ids: mcpServerIds.length ? mcpServerIds : undefined,
          // 未选模型时不落键,沿用会话默认/网关默认(§6.3 优先级)
          model: model ?? undefined,
        }),
      },
    ),
  attachments: (conversationId: string) =>
    http.request<Attachment[]>(
      `/api/v1/conversations/${conversationId}/attachments`,
    ),
  uploadAttachment: async (conversationId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(
      `${BASE}/api/v1/conversations/${conversationId}/attachments`,
      {
        method: 'POST',
        credentials: 'include',
        headers: await authHeaders(),
        body: form,
      },
    );
    if (!response.ok) {
      let code = 'attachment.upload_failed';
      let message = `附件上传失败(${response.status})`;
      try {
        const body = (await response.json()) as {
          code?: string;
          message?: string;
        };
        code = body.code ?? code;
        message = body.message ?? message;
      } catch {
        // 非 JSON 错误体保留兜底文案
      }
      throw new ApiError(response.status, code, message);
    }
    return (await response.json()) as Attachment;
  },
  downloadAttachment: async (attachmentId: string) => {
    const response = await fetch(`${BASE}/api/v1/attachments/${attachmentId}`, {
      credentials: 'include',
      headers: await authHeaders(),
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
        'attachment.download_failed',
        `附件下载失败(${response.status})`,
      );
    }
    return response.blob();
  },
};

/**
 * 流式发消息的事件回调(SSE):meta(分流结果)→ delta(增量)→ done;失败走 onError。
 * 事件名由后端标注,前端按名分发——不猜帧内容。
 */
export type StreamMessageHandlers = {
  onMeta?: (meta: {
    mode: MessagePostResult['mode'];
    message: ChatMessage;
    run: Run | null;
  }) => void;
  onDelta?: (text: string) => void;
  /**
   * 会话内只读工具调用(后端已执行完,事件仅为展示过程);
   * P1 起可能附带 risk/decision/error_code/duration_ms 等富字段,全部可缺省。
   */
  onTool?: (
    call: { name: string; arguments: string; ok: boolean } & ToolCallMeta,
  ) => void;
  onDone?: (result: {
    mode: MessagePostResult['mode'];
    reply: ChatMessage | null;
  }) => void;
  onError?: (error: { code: string; message: string }) => void;
  signal?: AbortSignal;
};

/** 解析一个具名 SSE 帧:`event: 名字` + `data: JSON`。 */
export function parseSseFrame(
  frame: string,
): [string, Record<string, unknown>] | null {
  const parsed = parseSseFrameText(frame);
  if (!parsed?.event || !parsed.data) {
    return null;
  }
  try {
    return [parsed.event, JSON.parse(parsed.data) as Record<string, unknown>];
  } catch {
    return null;
  }
}

/**
 * 流式发消息:模型边产出边回调 onDelta,页面据此增量渲染。
 *
 * 开流前的错误(404/409/422)仍是统一错误契约,直接抛 ApiError;
 * 开流后的失败由后端发 error 帧,走 onError(HTTP 已是 200,改不了)。
 * `model` 是本轮临时覆盖(§6.3 优先级高于会话默认),默认不传。
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  mode: SendMode,
  handlers: StreamMessageHandlers,
  attachmentIds: string[] = [],
  skillId?: string,
  mcpServerIds: string[] = [],
  model?: string,
): Promise<void> {
  await connectSse(
    `${BASE}/api/v1/conversations/${conversationId}/messages/stream`,
    {
      method: 'POST',
      body: JSON.stringify({
        content,
        mode,
        attachment_ids: attachmentIds,
        skill_id: skillId,
        // 空数组不落进 body:后端默认就是 [],旧契约测试也保持原样
        mcp_server_ids: mcpServerIds.length ? mcpServerIds : undefined,
        // 未选模型时不落键,沿用会话默认/网关默认(§6.3 优先级)
        model: model ?? undefined,
      }),
      signal: handlers.signal,
      mapError: async (response) => {
        // HTTP 层已是 200 但没流式内容;或开流前失败(404/409/422)。
        if (response.ok) {
          return new ApiError(
            response.status,
            'stream_failed',
            '响应没有流式内容',
          );
        }
        let code = 'stream_failed';
        let message = `发送失败(${response.status})`;
        try {
          const body = (await response.json()) as {
            code?: string;
            message?: string;
          };
          code = body.code ?? code;
          message = body.message ?? message;
        } catch {
          // 非 JSON 错误体:保留兜底文案
        }
        return new ApiError(response.status, code, message);
      },
      onFrame: (frame) => {
        // 复用既有帧解析器,保持「具名事件 + JSON data」的帧语义不变。
        const parsed = parseSseFrame(frame.raw);
        if (!parsed) {
          return;
        }
        const [name, data] = parsed;
        if (name === 'meta') {
          handlers.onMeta?.(
            data as Parameters<NonNullable<StreamMessageHandlers['onMeta']>>[0],
          );
        } else if (name === 'delta') {
          handlers.onDelta?.(typeof data.text === 'string' ? data.text : '');
        } else if (name === 'tool') {
          if (typeof data.name === 'string') {
            handlers.onTool?.({
              name: data.name,
              arguments:
                typeof data.arguments === 'string' ? data.arguments : '',
              ok: data.ok !== false,
              ...toolCallMetaFrom(data),
            });
          }
        } else if (name === 'done') {
          handlers.onDone?.(
            data as Parameters<NonNullable<StreamMessageHandlers['onDone']>>[0],
          );
        } else if (name === 'error') {
          handlers.onError?.({
            code:
              typeof data.code === 'string' ? data.code : 'llm.upstream_error',
            message:
              typeof data.message === 'string' ? data.message : '模型调用失败',
          });
        }
      },
    },
  );
}
