import { Alert } from '@beauty/design';

import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { AccountCard } from '@/features/settings/account-card';
import { ApiKeysPanel } from '@/features/settings/api-keys-panel';
import { ApprovalPolicyPanel } from '@/features/settings/approval-policy-panel';
import { ByokPanel } from '@/features/settings/byok-panel';
import { HooksPanel } from '@/features/settings/hooks-panel';
import { OAuthAppsPanel } from '@/features/settings/oauth-apps-panel';
import { SessionsCard } from '@/features/settings/sessions-card';
import { useSettingsModel } from '@/features/settings/use-settings-model';

/** 账号控制台:账号资料、多端会话与 BYOK/API Key/授权应用(§3/§7)。 */
export function SettingsPage() {
  const model = useSettingsModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="账号设置"
        subtitle="账号资料 · 多端会话 · BYOK / API Key / 授权应用 · 工具审批策略 · 自动化 hooks"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void model.refreshAccount()} />

        {model.unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再管理账号。
          </Alert>
        ) : null}

        <div className="mt-6 grid gap-6">
          <AccountCard model={model} />
          <ApprovalPolicyPanel />
          <HooksPanel />
          <SessionsCard
            sessions={model.sessions}
            device={model.device}
            setDevice={model.setDevice}
            canAddSession={model.canAddSession}
            addSession={() => model.addSessionMutation.mutate()}
            addSessionPending={model.addSessionMutation.isPending}
            addSessionError={model.addSessionMutation.error}
            revokeSession={(sessionId) =>
              model.revokeSessionMutation.mutate(sessionId)
            }
            revokePending={model.revokeSessionMutation.isPending}
          />
          <div className="grid gap-6">
            <ByokPanel />
            <ApiKeysPanel />
            <OAuthAppsPanel />
          </div>
        </div>
      </div>
    </div>
  );
}
