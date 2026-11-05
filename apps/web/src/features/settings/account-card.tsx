/**
 * 账号卡片:邮箱、显示名与状态。账号即隔离边界,邮箱/状态只读,显示名可改。
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
  Spin,
  Tag,
} from '@beauty/design';

import type { SettingsModel } from './use-settings-model';

export function AccountCard({ model }: { model: SettingsModel }) {
  const {
    account,
    accountLoading,
    displayNameValue,
    setDisplayName,
    updateMeMutation,
  } = model;

  return (
    <Card>
      <CardHeader>
        <CardTitle>账号</CardTitle>
        <CardDescription>
          账号是你唯一的身份与数据边界;邮箱与状态由系统维护,显示名可改。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {accountLoading ? (
          <Spin label="加载中…" />
        ) : (
          <>
            <Field label="邮箱" htmlFor="account-email">
              <Input id="account-email" value={account?.email ?? ''} disabled />
            </Field>
            <Field label="显示名" htmlFor="account-display-name">
              <Input
                id="account-display-name"
                value={displayNameValue}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="怎么称呼你"
              />
            </Field>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">状态</span>
              {account?.status === 'disabled' ? (
                <Tag color="danger">已停用</Tag>
              ) : (
                <Tag color="success">正常</Tag>
              )}
            </div>
            <div className="flex items-center gap-3">
              <Button
                onClick={() => updateMeMutation.mutate(displayNameValue)}
                disabled={updateMeMutation.isPending}
              >
                {updateMeMutation.isPending ? '保存中…' : '保存'}
              </Button>
              {updateMeMutation.isSuccess ? (
                <span className="text-sm text-muted-foreground">已保存</span>
              ) : null}
              {updateMeMutation.isError ? (
                <span className="text-sm text-destructive">
                  {(updateMeMutation.error as Error).message}
                </span>
              ) : null}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
