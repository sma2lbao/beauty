/**
 * 编辑重发 / 重新生成失败的就地中文文案(契约见 interaction-closure §2.1)。
 * 只把 `conversation.*` 与鉴权/额度三类翻译成用户可读句子,其它保留服务端 message。
 */
import { ApiError } from '@/lib/http';

/** `conversation.*` 错误码 → 用户可读文案。 */
const REWRITE_ERROR_TEXT: Record<string, string> = {
  'conversation.content_required': '编辑后的内容不能为空。',
  'conversation.content_not_allowed': '重新生成不需要填写内容。',
  'conversation.message_not_latest': '只能重写最后一轮消息,请刷新后再试。',
  'conversation.run_active': '这条消息关联的任务还没结束,先等它完成或取消。',
  'conversation.message_not_found': '消息不存在或已被删除。',
  'conversation.not_found': '会话不存在或已被删除。',
};

export function rewriteErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = REWRITE_ERROR_TEXT[error.code];
    if (mapped) return mapped;
    if (error.status === 401) return '登录已失效,请重新登录后再试。';
    if (error.status === 429) return '本月额度已用完,请稍后再试或提升配额。';
    return error.message || '重写失败,请稍后再试。';
  }
  return error instanceof Error && error.message
    ? error.message
    : '重写失败,请稍后再试。';
}
