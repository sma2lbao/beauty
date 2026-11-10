/**
 * 设置页「工具审批策略」面板(P1-A §7.1)。
 *
 * 表格展示账号级规则(工具级兜底 + 参数级优先命中),新增/删除都在本地草稿上
 * 进行,点「保存策略」整表 PUT。参数条件为空的行显示「—」并代表该工具的全部调用。
 * 「始终允许」的手工授权会以工具级 auto 行出现在这里,删行保存即可恢复确认。
 */
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Select,
  Spin,
} from '@beauty/design';
import { Trash2 } from 'lucide-react';

import { ToneBadge } from '@/components/console/ui';
import type { ApprovalAction, ApprovalArgMatch } from '@/lib/approval-api';

import {
  ARG_MATCH_VALUES,
  useApprovalPolicyModel,
} from './use-approval-policy-model';

const ACTION_LABELS: Record<ApprovalAction, string> = {
  auto: '自动执行',
  confirm: '需确认',
  deny: '禁止',
};

const ACTION_TONES: Record<
  ApprovalAction,
  'success' | 'warning' | 'danger'
> = {
  auto: 'success',
  confirm: 'warning',
  deny: 'danger',
};

const ACTION_OPTIONS: ApprovalAction[] = ['auto', 'confirm', 'deny'];

const MATCH_LABELS: Record<ApprovalArgMatch, string> = {
  exact: '精确匹配',
  prefix: '前缀匹配',
  glob: '通配符',
};

export function ApprovalPolicyPanel() {
  const model = useApprovalPolicyModel();

  return (
    <Card>
      <CardHeader>
        <CardTitle>工具审批策略</CardTitle>
        <CardDescription>
          参数级规则比工具级更具体、优先命中;「始终允许」授权会自动出现在这里——
          要恢复确认,删掉对应工具行并保存。空表时按风险等级:只读/写自动执行,
          危险操作需确认。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {model.loading ? <Spin label="加载审批策略…" /> : null}

        {model.loadError ? (
          <Alert variant="danger">
            读取审批策略失败:{model.loadError.message}
            <Button
              variant="outline"
              size="sm"
              className="ml-2"
              onClick={model.refetch}
            >
              重试
            </Button>
          </Alert>
        ) : null}

        {model.rows.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-ha-border">
            <table className="w-full border-collapse text-left text-[12px]">
              <thead>
                <tr className="bg-ha-panel-2 text-[10px] uppercase tracking-[0.12em] text-ha-faint">
                  <th className="px-3 py-2 font-medium">工具</th>
                  <th className="px-3 py-2 font-medium">参数条件</th>
                  <th className="px-3 py-2 font-medium">动作</th>
                  <th className="px-3 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {model.rows.map((row) => (
                  <tr key={row.id} className="border-t border-ha-border">
                    <td className="px-3 py-2">
                      <code className="font-mono text-[12px] text-ha-text">
                        {row.tool}
                      </code>
                    </td>
                    <td className="px-3 py-2">
                      {row.argName ? (
                        <span className="font-mono text-[11px] text-ha-text-2">
                          {row.argName} {row.argMatch} {row.argValue}
                        </span>
                      ) : (
                        <span className="text-ha-faint">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <ToneBadge tone={ACTION_TONES[row.action]}>
                        {ACTION_LABELS[row.action]}
                      </ToneBadge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`删除 ${row.tool} 的规则`}
                        disabled={model.saving}
                        onClick={() => model.removeRow(row.id)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : model.loading || model.loadError ? null : (
          <p className="text-[12px] leading-5 text-ha-muted">
            还没有规则:默认按风险等级处理——只读/写自动执行,危险操作需确认。
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={!model.dirty || model.saving}
            onClick={model.save}
          >
            {model.saving ? '保存中…' : '保存策略'}
          </Button>
          {model.dirty ? (
            <Button
              variant="outline"
              size="sm"
              disabled={model.saving}
              onClick={model.discard}
            >
              放弃修改
            </Button>
          ) : null}
          {model.saved ? (
            <span className="text-[12px] text-ha-success">已保存</span>
          ) : null}
        </div>

        {model.validationError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            {model.validationError}
          </p>
        ) : null}
        {model.saveError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            保存失败:{model.saveError.message}
          </p>
        ) : null}

        <form
          className="rounded-md border border-ha-border bg-ha-panel-2 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            model.addRow();
          }}
        >
          <p className="text-[12px] font-medium text-ha-text-2">新增规则</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="工具名" htmlFor="approval-tool">
              <Input
                id="approval-tool"
                value={model.form.tool}
                placeholder="exec 或 *"
                onChange={(event) =>
                  model.updateForm({ tool: event.target.value })
                }
              />
            </Field>
            <Field label="动作" htmlFor="approval-action">
              <Select
                id="approval-action"
                value={model.form.action}
                onChange={(event) =>
                  model.updateForm({
                    action: event.target.value as ApprovalAction,
                  })
                }
              >
                {ACTION_OPTIONS.map((action) => (
                  <option key={action} value={action}>
                    {ACTION_LABELS[action]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="参数名(可选)" htmlFor="approval-arg-name">
              <Input
                id="approval-arg-name"
                value={model.form.argName}
                placeholder="command"
                onChange={(event) =>
                  model.updateForm({ argName: event.target.value })
                }
              />
            </Field>
            <Field label="匹配方式" htmlFor="approval-arg-match">
              <Select
                id="approval-arg-match"
                value={model.form.argMatch}
                onChange={(event) =>
                  model.updateForm({
                    argMatch: event.target.value as ApprovalArgMatch,
                  })
                }
              >
                {ARG_MATCH_VALUES.map((match) => (
                  <option key={match} value={match}>
                    {MATCH_LABELS[match]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="参数值" htmlFor="approval-arg-value">
              <Input
                id="approval-arg-value"
                value={model.form.argValue}
                placeholder="git status"
                onChange={(event) =>
                  model.updateForm({ argValue: event.target.value })
                }
              />
            </Field>
          </div>
          <div className="mt-2 flex justify-end">
            <Button type="submit" variant="outline" size="sm">
              添加规则
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
