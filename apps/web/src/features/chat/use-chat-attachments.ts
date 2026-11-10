import { useRef, useState } from 'react';

import type { Attachment } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';

/** 单条消息的附件个数上限(与后端 max_attachments_per_message 对齐)。 */
export const MAX_ATTACHMENTS = 5;
/** 单个附件大小上限:后端 max_attachment_bytes 默认 10MB。 */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
/** 与后端 attach.attachment_too_large 同义的本地前置提示。 */
export const ATTACHMENT_TOO_LARGE = '附件超过 10MB 上限';

export type ChatAttachments = ReturnType<typeof useChatAttachments>;

/**
 * 输入区的附件选择与上传。
 *
 * 会话还不存在时(新会话)文件先暂存在本地,发送时先建会话再上传——
 * 这样首条消息也能带附件;已有会话则选完即传,与旧行为一致。
 * 上传/校验细节留在这里,use-chat-stream 只负责发送顺序。
 */
export function useChatAttachments(activeConversationId: string | null) {
  const [uploaded, setUploaded] = useState<Attachment[]>([]);
  const [staged, setStaged] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const total = uploaded.length + staged.length;

  /**
   * 选择文件:超过个数/大小上限的部分不静默丢,能加的照加并给出原因。
   * 有会话就立刻上传;没有会话就暂存,等发送时随建会话一起落地上传。
   */
  const pick = async (files: FileList | null) => {
    if (!files?.length) return;
    const picked = Array.from(files);
    const room = Math.max(MAX_ATTACHMENTS - total, 0);
    const tooLarge = picked.filter((file) => file.size > MAX_ATTACHMENT_BYTES);
    const accepted = picked
      .filter((file) => file.size <= MAX_ATTACHMENT_BYTES)
      .slice(0, room);
    setError(
      tooLarge.length
        ? ATTACHMENT_TOO_LARGE
        : accepted.length < picked.length
          ? `最多 ${MAX_ATTACHMENTS} 个附件,多余的已忽略`
          : null,
    );
    try {
      if (!accepted.length) return;
      if (!activeConversationId) {
        setStaged((previous) => [...previous, ...accepted]);
        return;
      }
      setUploading(true);
      const added: Attachment[] = [];
      for (const file of accepted) {
        added.push(
          await conversationsApi.uploadAttachment(activeConversationId, file),
        );
      }
      setUploaded((previous) => [...previous, ...added]);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '附件上传失败');
    } finally {
      setUploading(false);
      // 清空选择值:同一个文件再选一次也要触发 change
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  /**
   * 建会话后把暂存文件落地上传,返回可直接放进 streamMessage 的附件列表。
   * 失败时不清暂存(用户可重试),错误由调用方的发送失败通道展示。
   */
  const uploadStaged = async (conversationId: string) => {
    if (!staged.length) return uploaded;
    setUploading(true);
    try {
      const added: Attachment[] = [];
      for (const file of staged) {
        added.push(
          await conversationsApi.uploadAttachment(conversationId, file),
        );
      }
      const all = [...uploaded, ...added];
      setUploaded(all);
      setStaged([]);
      return all;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '附件上传失败');
      throw failure;
    } finally {
      setUploading(false);
    }
  };

  const removeUploaded = (attachmentId: string) =>
    setUploaded((previous) =>
      previous.filter((item) => item.id !== attachmentId),
    );
  const removeStaged = (index: number) =>
    setStaged((previous) =>
      previous.filter((_, position) => position !== index),
    );
  const clear = () => {
    setUploaded([]);
    setStaged([]);
  };
  const restore = (attachments: Attachment[]) => setUploaded(attachments);

  return {
    uploaded,
    staged,
    uploading,
    error,
    fileInputRef,
    pick,
    uploadStaged,
    removeUploaded,
    removeStaged,
    clear,
    restore,
  };
}
