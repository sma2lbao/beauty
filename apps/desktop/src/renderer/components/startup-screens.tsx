/**
 * 启动期的三种整页状态:缺少桌面桥接、读取中、读取失败。
 *
 * 缺少桥接是最容易被忽略的路径(浏览器直接打开渲染进程):必须给出可读说明,
 * 不能白屏,也不能在模块顶层访问 window.beautyDesktop 时抛错。
 */
import { Empty } from '@beauty/design';
import { AlertTriangle, Loader2 } from 'lucide-react';

import { ConsoleButton } from '@/components/ui';

export function MissingBridgePage() {
  return (
    <div className="grid h-full place-items-center bg-ha-bg p-8">
      <div className="flex max-w-md flex-col items-center gap-2 text-center">
        <Empty description="请通过桌面应用启动" />
        <p className="-mt-4 text-[12px] leading-5 text-ha-dim">
          当前页面没有检测到 Electron 预加载桥接(window.beautyDesktop),
          无法读取本地设置、文件与命令。请使用 beauty moon 桌面应用打开。
        </p>
      </div>
    </div>
  );
}

export function BootLoadingPage() {
  return (
    <div className="grid h-full place-items-center bg-ha-bg p-8">
      <div className="flex items-center gap-2.5 font-mono text-[12px] text-ha-dim">
        <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
        正在读取本地设置…
      </div>
    </div>
  );
}

export function BootErrorPage({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div className="grid h-full place-items-center bg-ha-bg p-8">
      <div className="flex w-full max-w-md flex-col gap-3 rounded-lg border border-ha-danger-border bg-ha-panel p-4">
        <div className="flex items-center gap-2">
          <AlertTriangle
            aria-hidden="true"
            className="h-4 w-4 shrink-0 text-ha-danger"
          />
          <p className="text-[13px] font-semibold text-ha-text">启动失败</p>
        </div>
        <p
          role="alert"
          className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[12px] leading-5 text-ha-danger"
        >
          {error ?? '读取本地设置失败'}
        </p>
        <div className="flex justify-end">
          <ConsoleButton variant="secondary" onClick={onRetry}>
            重试
          </ConsoleButton>
        </div>
      </div>
    </div>
  );
}
