import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Input,
  Select,
  Spin,
  Textarea,
} from '@beauty/design';

import { AuthBar } from '@/components/runs/auth-bar';
import type { Role } from '@/lib/account-api';
import { accountApi } from '@/lib/account-api';
import { isUnauthorized } from '@/lib/http';

const FIELD_CLASS = 'mt-1';
const LABEL_CLASS = 'block text-sm font-medium';

const ORG_KEY = ['account-organization'];
const MEMBERS_KEY = ['account-members'];
const SESSIONS_KEY = ['account-sessions'];

function formatTime(value: string | null): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN');
}

/** 账号控制台:组织档案/配额、成员、多端会话(§3/§7)。 */
export function SettingsPage() {
  const queryClient = useQueryClient();
  // 表单值 = 本地编辑值 ?? 服务端值:不在 queryFn 里 setState(避免派生状态同步)
  const [orgName, setOrgName] = useState<string | null>(null);
  const [orgPlan, setOrgPlan] = useState<string | null>(null);
  const [quotaText, setQuotaText] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [subject, setSubject] = useState('');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<Role>('member');
  const [device, setDevice] = useState('');
  const [sessionUserId, setSessionUserId] = useState('');

  const orgQuery = useQuery({ queryKey: ORG_KEY, queryFn: accountApi.organization });
  const membersQuery = useQuery({ queryKey: MEMBERS_KEY, queryFn: accountApi.members });
  const sessionsQuery = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: accountApi.sessions,
  });

  const refreshOrg = () => queryClient.invalidateQueries({ queryKey: ORG_KEY });
  const refreshMembers = () =>
    queryClient.invalidateQueries({ queryKey: MEMBERS_KEY });
  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });

  const orgMutation = useMutation({
    mutationFn: async (input: { name: string; plan: string; quota: string }) => {
      let quota: Record<string, unknown> | undefined;
      if (input.quota.trim()) {
        try {
          quota = JSON.parse(input.quota) as Record<string, unknown>;
        } catch {
          throw new Error('配额必须是合法 JSON,例如 {"seats": 20}');
        }
      }
      return accountApi.updateOrganization({
        name: input.name.trim() || undefined,
        plan: input.plan.trim() || undefined,
        quota,
      });
    },
    onSuccess: () => {
      setQuotaError(null);
      setQuotaText(null); // 回到服务端值
      void refreshOrg();
    },
    onError: (error: Error) => setQuotaError(error.message),
  });

  const addMemberMutation = useMutation({
    mutationFn: () =>
      accountApi.addMember({
        subject: subject.trim(),
        email: email.trim(),
        display_name: displayName.trim() || null,
        role,
      }),
    onSuccess: async () => {
      setSubject('');
      setEmail('');
      setDisplayName('');
      await refreshMembers();
    },
  });

  const updateMemberMutation = useMutation({
    mutationFn: (input: {
      memberId: string;
      patch: { role?: Role; status?: 'active' | 'disabled' };
    }) => accountApi.updateMember(input.memberId, input.patch),
    onSuccess: refreshMembers,
  });

  const addSessionMutation = useMutation({
    mutationFn: () =>
      accountApi.addSession({
        user_id: sessionUserId.trim(),
        device: device.trim(),
      }),
    onSuccess: async () => {
      setDevice('');
      await refreshSessions();
    },
  });

  const revokeSessionMutation = useMutation({
    mutationFn: (sessionId: string) => accountApi.revokeSession(sessionId),
    onSuccess: refreshSessions,
  });

  const organization = orgQuery.data;
  const nameValue = orgName ?? organization?.name ?? '';
  const planValue = orgPlan ?? organization?.plan ?? '';
  const quotaValue = quotaText ?? JSON.stringify(organization?.quota ?? {});
  const members = membersQuery.data ?? [];
  const sessions = sessionsQuery.data ?? [];
  const errors = [
    orgQuery.error,
    membersQuery.error,
    sessionsQuery.error,
    orgMutation.error,
    addMemberMutation.error,
    updateMemberMutation.error,
    addSessionMutation.error,
    revokeSessionMutation.error,
  ].filter(Boolean);
  const unauthorized = errors.some(isUnauthorized);
  const canAddMember = subject.trim().length > 0 && email.trim().length > 0;
  const canAddSession = sessionUserId.trim().length > 0 && device.trim().length > 0;

  return (
    <section className="mx-auto max-w-6xl px-6 py-12">
      <h1 className="font-display text-3xl">账号与组织</h1>
      <p className="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
        组织档案与配额、成员名单、多端会话。认证本身由 IdP 负责(§10),
        这里只维护业务侧的组织结构。
      </p>

      <div className="mt-6">
        <AuthBar onChange={() => void refreshOrg()} />
      </div>

      {unauthorized ? (
        <Alert variant="danger" className="mt-4">
          未登录或令牌失效:先在上方面板完成鉴权,再管理账号。
        </Alert>
      ) : null}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>组织档案</CardTitle>
            <CardDescription>
              组织 id 即租户标识:{organization?.id ?? '读取中…'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className={LABEL_CLASS} htmlFor="org-name">
                组织名称
              </label>
              <Input
                id="org-name"
                className={FIELD_CLASS}
                value={nameValue}
                onChange={(event) => setOrgName(event.target.value)}
              />
            </div>
            <div>
              <label className={LABEL_CLASS} htmlFor="org-plan">
                套餐
              </label>
              <Input
                id="org-plan"
                className={FIELD_CLASS}
                value={planValue}
                onChange={(event) => setOrgPlan(event.target.value)}
                placeholder="free / team / enterprise"
              />
            </div>
            <div>
              <label className={LABEL_CLASS} htmlFor="org-quota">
                配额(JSON)
              </label>
              <Textarea
                id="org-quota"
                className="mt-1 min-h-24 font-mono"
                value={quotaValue}
                onChange={(event) => setQuotaText(event.target.value)}
                placeholder='{"seats": 20, "runs_per_month": 500}'
              />
            </div>
            {quotaError ? (
              <p className="text-sm text-destructive">{quotaError}</p>
            ) : null}
            <div className="flex items-center gap-3">
              <Button
                onClick={() =>
                  orgMutation.mutate({
                    name: nameValue,
                    plan: planValue,
                    quota: quotaValue,
                  })
                }
                disabled={orgMutation.isPending}
              >
                {orgMutation.isPending ? '保存中…' : '保存'}
              </Button>
              {orgMutation.isSuccess ? (
                <span className="text-sm text-muted-foreground">已保存</span>
              ) : null}
              {orgMutation.isError && !quotaError ? (
                <span className="text-sm text-destructive">
                  {(orgMutation.error as Error).message}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>邀请成员</CardTitle>
            <CardDescription>
              subject 必须与 IdP 签发的 sub 一致,否则该成员登录后对不上号。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className={LABEL_CLASS} htmlFor="member-subject">
                IdP subject
              </label>
              <Input
                id="member-subject"
                className={FIELD_CLASS}
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                placeholder="idp|user-1"
              />
            </div>
            <div>
              <label className={LABEL_CLASS} htmlFor="member-email">
                邮箱
              </label>
              <Input
                id="member-email"
                className={FIELD_CLASS}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="someone@example.com"
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className={LABEL_CLASS} htmlFor="member-name">
                  显示名
                </label>
                <Input
                  id="member-name"
                  className={FIELD_CLASS}
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </div>
              <div>
                <label className={LABEL_CLASS} htmlFor="member-role">
                  角色
                </label>
                <Select
                  id="member-role"
                  className={FIELD_CLASS}
                  value={role}
                  onChange={(event) => setRole(event.target.value as Role)}
                >
                  <option value="member">member</option>
                  <option value="owner">owner</option>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Button
                onClick={() => addMemberMutation.mutate()}
                disabled={!canAddMember || addMemberMutation.isPending}
              >
                {addMemberMutation.isPending ? '添加中…' : '添加成员'}
              </Button>
              {addMemberMutation.isError ? (
                <span className="text-sm text-destructive">
                  {(addMemberMutation.error as Error).message}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>成员</CardTitle>
          <CardDescription>共 {members.length} 人;停用不删除,保留审计。</CardDescription>
        </CardHeader>
        <CardContent>
          {membersQuery.isLoading ? (
            <Spin label="加载中…" />
          ) : members.length === 0 ? (
            <Empty description="还没有成员:先在上方添加。" />
          ) : (
            <ul className="divide-y">
              {members.map((member) => (
                <li
                  key={member.id}
                  className="flex flex-wrap items-center justify-between gap-3 py-3"
                >
                  <div>
                    <p className="font-medium">
                      {member.display_name ?? member.email}
                      {member.status === 'disabled' ? (
                        <span className="ml-2 text-xs text-muted-foreground">
                          (已停用)
                        </span>
                      ) : null}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {member.email} · {member.subject} · 加入于{' '}
                      {formatTime(member.created_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Select
                      aria-label={`${member.email} 的角色`}
                      className="w-28"
                      size="sm"
                      value={member.role}
                      onChange={(event) =>
                        updateMemberMutation.mutate({
                          memberId: member.id,
                          patch: { role: event.target.value as Role },
                        })
                      }
                    >
                      <option value="member">member</option>
                      <option value="owner">owner</option>
                    </Select>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        updateMemberMutation.mutate({
                          memberId: member.id,
                          patch: {
                            status:
                              member.status === 'active' ? 'disabled' : 'active',
                          },
                        })
                      }
                      disabled={updateMemberMutation.isPending}
                    >
                      {member.status === 'active' ? '停用' : '启用'}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>设备会话</CardTitle>
          <CardDescription>
            多端登录登记(app 预留,§7);吊销后该会话立即失效。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <label className={LABEL_CLASS} htmlFor="session-user">
                成员
              </label>
              <Select
                id="session-user"
                className={FIELD_CLASS}
                value={sessionUserId}
                onChange={(event) => setSessionUserId(event.target.value)}
              >
                <option value="">选择成员…</option>
                {members.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.display_name ?? member.email}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-w-56 flex-1">
              <label className={LABEL_CLASS} htmlFor="session-device">
                设备
              </label>
              <Input
                id="session-device"
                className={FIELD_CLASS}
                value={device}
                onChange={(event) => setDevice(event.target.value)}
                placeholder="MacBook / Chrome"
              />
            </div>
            <Button
              onClick={() => addSessionMutation.mutate()}
              disabled={!canAddSession || addSessionMutation.isPending}
            >
              {addSessionMutation.isPending ? '登记中…' : '登记会话'}
            </Button>
          </div>
          {addSessionMutation.isError ? (
            <p className="text-sm text-destructive">
              {(addSessionMutation.error as Error).message}
            </p>
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
                    <p className="mt-1 text-xs text-muted-foreground">
                      {session.status} · 成员 {session.user_id.slice(0, 8)}… ·{' '}
                      {formatTime(session.created_at)}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => revokeSessionMutation.mutate(session.id)}
                    disabled={
                      session.status !== 'active' || revokeSessionMutation.isPending
                    }
                  >
                    {session.status === 'active' ? '吊销' : '已吊销'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
