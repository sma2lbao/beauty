/**
 * 成员列表卡片:角色切换与启停用。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Empty,
  Select,
  Spin,
} from '@beauty/design';

import type { Member, Role } from '@/lib/account-api';
import { formatDateTime } from '@/lib/presentation';

type UpdateInput = {
  memberId: string;
  patch: { role?: Role; status?: 'active' | 'disabled' };
};

type Props = {
  members: Member[];
  loading: boolean;
  updateMember: (input: UpdateInput) => void;
  updatePending: boolean;
};

export function MembersCard({
  members,
  loading,
  updateMember,
  updatePending,
}: Props) {
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>成员</CardTitle>
        <CardDescription>
          共 {members.length} 人;停用不删除,保留审计。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
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
                    {formatDateTime(member.created_at, '—')}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Select
                    aria-label={`${member.email} 的角色`}
                    className="w-28"
                    size="sm"
                    value={member.role}
                    onChange={(event) =>
                      updateMember({
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
                      updateMember({
                        memberId: member.id,
                        patch: {
                          status:
                            member.status === 'active' ? 'disabled' : 'active',
                        },
                      })
                    }
                    disabled={updatePending}
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
  );
}
