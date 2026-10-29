import { Alert } from '@beauty/design';

import { ApiKeysPanel } from '@/features/settings/api-keys-panel';
import { ByokPanel } from '@/features/settings/byok-panel';
import { OAuthAppsPanel } from '@/features/settings/oauth-apps-panel';
import { PageHeader } from '@/components/console/ui';
import { AuthBar } from '@/features/auth/auth-bar';
import { InviteMemberCard } from '@/features/settings/invite-member-card';
import { MembersCard } from '@/features/settings/members-card';
import { OrganizationCard } from '@/features/settings/organization-card';
import { SessionsCard } from '@/features/settings/sessions-card';
import { useSettingsModel } from '@/features/settings/use-settings-model';

/** 账号控制台:组织档案/配额、成员、多端会话(§3/§7)。 */
export function SettingsPage() {
  const model = useSettingsModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="账号与组织"
        subtitle="组织档案与配额 · 成员名单 · 多端会话 · 认证由 IdP 负责"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <AuthBar onChange={() => void model.refreshOrg()} />

        {model.unauthorized ? (
          <Alert variant="danger" className="mt-4">
            未登录或令牌失效:先在上方面板完成鉴权,再管理账号。
          </Alert>
        ) : null}

        <div className="mt-6 grid gap-6">
          <ByokPanel />
          <ApiKeysPanel />
          <OAuthAppsPanel />
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <OrganizationCard model={model} />
          <InviteMemberCard
            subject={model.subject}
            email={model.email}
            displayName={model.displayName}
            role={model.role}
            setSubject={model.setSubject}
            setEmail={model.setEmail}
            setDisplayName={model.setDisplayName}
            setRole={model.setRole}
            canAddMember={model.canAddMember}
            addMemberMutation={model.addMemberMutation}
          />
        </div>

        <MembersCard
          members={model.members}
          loading={model.membersLoading}
          updateMember={(input) => model.updateMemberMutation.mutate(input)}
          updatePending={model.updateMemberMutation.isPending}
        />

        <SessionsCard
          members={model.members}
          sessions={model.sessions}
          sessionUserId={model.sessionUserId}
          device={model.device}
          setSessionUserId={model.setSessionUserId}
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
      </div>
    </div>
  );
}
