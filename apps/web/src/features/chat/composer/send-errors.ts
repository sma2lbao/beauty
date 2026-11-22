/**
 * 发送失败的可行动文案:后端错误码 → 用户能照做的下一步。
 *
 * 引用类错误在开流前返回(草稿与引用都还在输入区),所以文案要指向
 * 「移除/减少引用后重发」;连接器类错误指向连接器页。
 */
import { ApiError } from '@/lib/http';

const MESSAGES: Record<string, string> = {
  'context_ref.not_found': '引用的资料已被删除,请移除后重发',
  'context_ref.invalid_path': '文件路径不合法,请重新选择沙箱文件',
  'context_ref.empty': '这份资料没有可引用的正文,请换一份',
  'context_ref.unavailable': '引用能力未开启,请联系管理员',
  'context_ref.too_large': '引用内容过长,请减少引用后重发',
  'context_ref.limit_exceeded': '引用数量过多,请减少引用后重发',
  'connector.not_found': '连接器未连接或没有可用工具,请去连接器页检查',
  'connector.unavailable': '连接器能力未开启,请联系管理员',
  'connector.limit_exceeded': '选择的连接器过多,请减少后重发',
};

/** 已知引用/连接器错误给行动建议;其余退回服务端 message。 */
export function describeSendError(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = MESSAGES[error.code];
    if (mapped) return mapped;
    if (error.status === 422) return '引用数量过多或参数不合法,请调整后重发';
    if (error.status === 400) return '引用参数不合法,请重新选择资料';
  }
  if (error instanceof Error) return error.message;
  return '发送失败';
}
