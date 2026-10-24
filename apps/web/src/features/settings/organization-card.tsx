/**
 * 组织档案卡片:名称/套餐/配额 JSON 的编辑与保存。
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
  Textarea,
} from '@beauty/design';

import type { SettingsModel } from './use-settings-model';

export function OrganizationCard({ model }: { model: SettingsModel }) {
  const {
    organization,
    nameValue,
    planValue,
    quotaValue,
    setOrgName,
    setOrgPlan,
    setQuotaText,
    quotaError,
    orgMutation,
  } = model;

  return (
    <Card>
      <CardHeader>
        <CardTitle>组织档案</CardTitle>
        <CardDescription>
          组织 id 即租户标识:{organization?.id ?? '读取中…'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="组织名称" htmlFor="org-name">
          <Input
            id="org-name"
            value={nameValue}
            onChange={(event) => setOrgName(event.target.value)}
          />
        </Field>
        <Field label="套餐" htmlFor="org-plan">
          <Input
            id="org-plan"
            value={planValue}
            onChange={(event) => setOrgPlan(event.target.value)}
            placeholder="free / team / enterprise"
          />
        </Field>
        <Field label="配额(JSON)" htmlFor="org-quota">
          <Textarea
            id="org-quota"
            className="min-h-24 font-mono"
            value={quotaValue}
            onChange={(event) => setQuotaText(event.target.value)}
            placeholder='{"seats": 20, "runs_per_month": 500}'
          />
        </Field>
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
  );
}
