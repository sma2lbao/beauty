/**
 * 邀请成员卡片:IdP subject / 邮箱 / 显示名 / 角色。
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
  Select,
} from '@beauty/design';

import type { Role } from '@/lib/account-api';

type Props = {
  subject: string;
  email: string;
  displayName: string;
  role: Role;
  setSubject: (value: string) => void;
  setEmail: (value: string) => void;
  setDisplayName: (value: string) => void;
  setRole: (value: Role) => void;
  canAddMember: boolean;
  addMemberMutation: {
    mutate: () => void;
    isPending: boolean;
    isError: boolean;
    error: Error | null;
  };
};

export function InviteMemberCard({
  subject,
  email,
  displayName,
  role,
  setSubject,
  setEmail,
  setDisplayName,
  setRole,
  canAddMember,
  addMemberMutation,
}: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>邀请成员</CardTitle>
        <CardDescription>
          subject 必须与 IdP 签发的 sub 一致,否则该成员登录后对不上号。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="IdP subject" htmlFor="member-subject">
          <Input
            id="member-subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            placeholder="idp|user-1"
          />
        </Field>
        <Field label="邮箱" htmlFor="member-email">
          <Input
            id="member-email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="someone@example.com"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="显示名" htmlFor="member-name">
            <Input
              id="member-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </Field>
          <Field label="角色" htmlFor="member-role">
            <Select
              id="member-role"
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              <option value="member">member</option>
              <option value="owner">owner</option>
            </Select>
          </Field>
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
  );
}
