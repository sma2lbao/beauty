import { Alert } from '@beauty/design';

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { OutboxEventsCard } from '@/features/outbox/outbox-events-card';
import { OutboxStatsCards } from '@/features/outbox/outbox-stats-cards';
import { useOutboxModel } from '@/features/outbox/use-outbox-model';

/** 事件队列运维页:看 outbox 状态、人工重放失败事件(§6.3)。 */
export function OutboxPage() {
  const model = useOutboxModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="事件队列"
        subtitle="发件箱(outbox):同事务落库、最低一次投递、退避重试,失败可人工重放"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void model.refresh()} />

        {model.unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再运维事件队列。
          </Alert>
        ) : null}

        {model.disabled ? (
          <Alert variant="neutral" className="mt-4 border-dashed">
            该服务未启用 outbox(
            <span className="font-mono">OUTBOX_ENABLED=false</span>
            ):没有事件流可运维。启用后重启服务即可。
          </Alert>
        ) : null}

        <OutboxStatsCards stats={model.stats} />
        <OutboxEventsCard model={model} />
      </div>
    </div>
  );
}
