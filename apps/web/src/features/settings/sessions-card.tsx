/**
 * 设备会话卡片:登记新会话与吊销既有会话(账号由 token 决定)。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Tag,
} from '@beauty/design';

import type { Session } from '@/lib/account-api';
import { formatDateTime } from '@/lib/presentation';

type Props = {
  sessions: Session[];
  device: string;
  setDevice: (value: string) => void;
  canAddSession: boolean;
  addSession: () => void;
  addSessionPending: boolean;
  addSessionError: Error | null;
  revokeSession: (sessionId: string) => void;
  revokePending: boolean;
};

export function SessionsCard({
  sessions,
  device,
  setDevice,
  canAddSession,
  addSession,
  addSessionPending,
  addSessionError,
  revokeSession,
  revokePending,
}: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>设备会话</CardTitle>
        <CardDescription>多端登录登记;吊销后该会话立即失效。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field
            className="min-w-56 flex-1"
            label="设备"
            htmlFor="session-device"
          >
            <Input
              id="session-device"
              value={device}
              onChange={(event) => setDevice(event.target.value)}
              placeholder="MacBook / Chrome"
            />
          </Field>
          <Button
            onClick={addSession}
            disabled={!canAddSession || addSessionPending}
          >
            {addSessionPending ? '登记中…' : '登记会话'}
          </Button>
        </div>
        {addSessionError ? (
          <p className="text-sm text-destructive">{addSessionError.message}</p>
        ) : null}

        {sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">还没有会话记录。</p>
        ) : (
          <ul className="divide-y">
            {sessions.map((session) => (
              <li
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <div>
                  <p className="font-medium">{session.device}</p>
                  <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {session.status === 'active' ? (
                      <Tag color="success">有效</Tag>
                    ) : (
                      <Tag color="neutral">已吊销</Tag>
                    )}
                    <span>
                      创建于 {formatDateTime(session.created_at, '—')}
                    </span>
                    {session.last_seen_at ? (
                      <span>
                        · 最近活跃 {formatDateTime(session.last_seen_at, '—')}
                      </span>
                    ) : null}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => revokeSession(session.id)}
                  disabled={session.status !== 'active' || revokePending}
                >
                  {session.status === 'active' ? '吊销' : '已吊销'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
