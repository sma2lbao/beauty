/**
 * 一次性 secret 弹窗(P1-B §8):创建或轮换 hook 后展示签名密钥。
 *
 * secret 只在创建/轮换响应里出现一次;关闭弹窗即从 state 丢弃,不写任何缓存。
 * 接收端按 X-DSH-Timestamp + '.' + body 做 HMAC-SHA256 校验 X-DSH-Signature。
 */
import { Button } from '@beauty/design';
import { Copy } from 'lucide-react';

import { ConsoleDialog } from '@/components/console/dialog';

import { EVENT_LABELS, type HookSecretState } from './use-hooks-model';

export type HookSecretDialogProps = {
  secret: HookSecretState | null;
  copied: boolean;
  onCopy: () => void;
  onClose: () => void;
};

export function HookSecretDialog({
  secret,
  copied,
  onCopy,
  onClose,
}: HookSecretDialogProps) {
  return (
    <ConsoleDialog
      open={secret !== null}
      title="hook 签名密钥(只显示一次)"
      description="关闭后无法再次查看;接收端用它校验 X-DSH-Signature。"
      onClose={onClose}
      footer={
        <Button variant="primary" size="sm" onClick={onClose}>
          我已保存,关闭
        </Button>
      }
    >
      {secret ? (
        <div className="space-y-3">
          <p className="text-[12px] leading-5 text-ha-text-2">
            目标地址:
            <code className="ml-1 break-all font-mono text-ha-text">
              {secret.target}
            </code>
          </p>
          <p className="text-[12px] text-ha-text-2">
            事件:{EVENT_LABELS[secret.event]}（{secret.event}）
          </p>
          <div>
            <p className="mb-1 text-xs font-medium text-ha-text-2">secret</p>
            <pre className="max-h-40 overflow-auto rounded-md border border-ha-warning-border bg-ha-panel-2 p-2 font-mono text-[12px] text-ha-warning">
              {secret.secret}
            </pre>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={onCopy}>
              <Copy className="mr-1 h-3.5 w-3.5" />
              {copied ? '已复制' : '复制密钥'}
            </Button>
            {copied ? (
              <span className="text-[11px] text-ha-success">
                已复制到剪贴板
              </span>
            ) : null}
          </div>
          <p className="text-[11px] leading-5 text-ha-muted">
            接收端校验:用 secret 对「X-DSH-Timestamp、英文句点与原始请求体
            拼接后的字符串」做 HMAC-SHA256,与 X-DSH-Signature:
            sha256=&lt;hex&gt; 比对;时间戳窗口由接收端自行判定。
          </p>
        </div>
      ) : null}
    </ConsoleDialog>
  );
}
