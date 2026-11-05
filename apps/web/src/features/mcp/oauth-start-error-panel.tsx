/**
 * oauth/start 失败分类面板:MCP 详情卡与连接器预设卡共用。
 *
 * 只吃 describeOAuthStartError 的结果,自己不做错误判断;按 cta 决定按钮
 * 主次:推荐动作放前面(重试或改用令牌),另一个作为备选仍然可见,
 * cta === 'none' 时不给按钮(部署/配置问题,点了也没用)。
 */
import { AlertTriangle, KeyRound, RotateCw } from 'lucide-react';

import { ConsoleButton } from '@/components/console/ui';
import { cn } from '@/lib/utils';

import type { OAuthStartErrorInfo } from './oauth-start-error';

export type OAuthStartErrorPanelProps = {
  error: OAuthStartErrorInfo;
  /** 重试浏览器授权 */
  onRetry: () => void;
  /** 改用静态令牌;不传表示没有可写令牌的 server(如尚未创建) */
  onUseToken?: () => void;
  /** 重试请求进行中(禁用按钮并改文案) */
  retrying?: boolean;
  /** 卡片自带主按钮时置 false,避免重复的「重试授权」 */
  showRetry?: boolean;
  className?: string;
};

/** 网络/注册临时故障偏警告色,其余(元数据/不支持)偏错误色。 */
function isWarningKind(info: OAuthStartErrorInfo): boolean {
  return info.kind === 'network' || info.kind === 'registration_failed';
}

export function OAuthStartErrorPanel({
  error,
  onRetry,
  onUseToken,
  retrying = false,
  showRetry = true,
  className,
}: OAuthStartErrorPanelProps) {
  const warning = isWarningKind(error);
  const primaryIsRetry = error.cta === 'retry';
  return (
    <div
      role="alert"
      className={cn(
        'rounded-md border px-3 py-2 text-[12px] leading-5',
        warning
          ? 'border-ha-warning-border bg-ha-warning-bg'
          : 'border-ha-danger-border bg-ha-danger-bg',
        className,
      )}
    >
      <p
        className={cn(
          'flex items-center gap-1.5 font-medium',
          warning ? 'text-ha-warning' : 'text-ha-danger',
        )}
      >
        <AlertTriangle aria-hidden className="size-3.5 shrink-0" />
        {error.title}
      </p>
      <p className="mt-1 text-ha-text-2">{error.detail}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {error.cta === 'none' ? null : (
          <>
            {showRetry ? (
              <ConsoleButton
                variant={primaryIsRetry ? 'primary' : 'secondary'}
                size="sm"
                disabled={retrying}
                onClick={onRetry}
              >
                <RotateCw
                  aria-hidden
                  className={cn('size-3.5', retrying && 'animate-spin')}
                />
                {retrying ? '重试中…' : '重试授权'}
              </ConsoleButton>
            ) : null}
            {onUseToken ? (
              <ConsoleButton
                variant={primaryIsRetry && showRetry ? 'secondary' : 'primary'}
                size="sm"
                onClick={onUseToken}
              >
                <KeyRound aria-hidden className="size-3.5" />
                改用令牌
              </ConsoleButton>
            ) : null}
          </>
        )}
        <span className="ml-auto font-mono text-[10px] text-ha-faint">
          错误码 {error.code}
        </span>
      </div>
    </div>
  );
}
