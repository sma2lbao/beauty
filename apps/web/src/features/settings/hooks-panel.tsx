/**
 * 设置页「自动化 hooks」面板(P1-B,design/hooks.md §8)。
 *
 * 五个生命周期挂点各配一条 HTTPS webhook:工具事件可拒绝/观察,压缩事件可跳过。
 * 表格提供启用开关、轮换签名密钥与删除;secret 只在创建/轮换后弹窗显示一次。
 * 部署总开关关闭(或后端返回 409 hooks.disabled)时整卡显示「当前部署未启用 hooks」,
 * 不做静默失败。
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
  Switch,
} from '@beauty/design';
import { RotateCw, Trash2 } from 'lucide-react';

import { HOOK_EVENTS, type HookEvent } from '@/lib/hooks-api';

import { HookSecretDialog } from './hook-secret-dialog';
import {
  EVENT_LABELS,
  HOOK_TIMEOUT_MAX,
  HOOK_TIMEOUT_MIN,
  hookErrorMessage,
  summarizeTarget,
  useHooksModel,
} from './use-hooks-model';

export function HooksPanel() {
  const model = useHooksModel();

  if (model.disabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>自动化 hooks</CardTitle>
          <CardDescription>
            把工具调用与会话生命周期事件 POST 到你自己的 HTTPS 接收端。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Alert variant="neutral">
            当前部署未启用 hooks:后端 HOOKS_ENABLED 开关关闭。启用后刷新本页即可配置。
          </Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>自动化 hooks</CardTitle>
        <CardDescription>
          五个生命周期挂点各配一条 HTTPS webhook:PreToolUse 可拒绝本次工具调用,
          PreCompact 可跳过本次压缩,其余事件只观察。secret 只在创建/轮换时显示一次,
          接收端需校验 X-DSH-Signature。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {model.loading ? <Spin label="加载 hooks…" /> : null}

        {model.loadError ? (
          <Alert variant="danger">
            读取 hooks 失败:{hookErrorMessage(model.loadError)}
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

        {model.createError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            创建失败:{hookErrorMessage(model.createError)}
          </p>
        ) : null}
        {model.rotateError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            轮换失败:{hookErrorMessage(model.rotateError)}
          </p>
        ) : null}
        {model.toggleError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            更新失败:{hookErrorMessage(model.toggleError)}
          </p>
        ) : null}
        {model.deleteError ? (
          <p role="alert" className="text-[12px] text-ha-danger">
            删除失败:{hookErrorMessage(model.deleteError)}
          </p>
        ) : null}

        {model.hooks.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-ha-border">
            <table className="w-full border-collapse text-left text-[12px]">
              <thead>
                <tr className="bg-ha-panel-2 text-[10px] uppercase tracking-[0.12em] text-ha-faint">
                  <th className="px-3 py-2 font-medium">事件</th>
                  <th className="px-3 py-2 font-medium">匹配</th>
                  <th className="px-3 py-2 font-medium">目标地址</th>
                  <th className="px-3 py-2 font-medium">超时</th>
                  <th className="px-3 py-2 font-medium">启用</th>
                  <th className="px-3 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {model.hooks.map((hook) => (
                  <tr key={hook.id} className="border-t border-ha-border">
                    <td className="px-3 py-2">
                      <span className="text-ha-text">
                        {EVENT_LABELS[hook.event]}
                      </span>
                      <span className="ml-2 font-mono text-[10px] text-ha-faint">
                        {hook.event}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <code className="font-mono text-[11px] text-ha-text-2">
                        {hook.matcher}
                      </code>
                    </td>
                    <td className="max-w-[220px] px-3 py-2">
                      <code
                        className="block truncate font-mono text-[11px] text-ha-text-2"
                        title={hook.target}
                      >
                        {summarizeTarget(hook.target)}
                      </code>
                    </td>
                    <td className="px-3 py-2 text-ha-text-2">
                      {hook.timeout_seconds}s
                    </td>
                    <td className="px-3 py-2">
                      <Switch
                        checked={hook.enabled}
                        aria-label={`启用 ${EVENT_LABELS[hook.event]} hook`}
                        disabled={model.toggling}
                        onCheckedChange={(enabled) =>
                          model.toggleHook(hook, enabled)
                        }
                      />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label={`轮换 ${EVENT_LABELS[hook.event]} hook 的签名密钥`}
                          disabled={model.rotatePending}
                          onClick={() => model.rotateHook(hook.id)}
                        >
                          <RotateCw className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          aria-label={`删除 ${EVENT_LABELS[hook.event]} hook`}
                          disabled={model.deleting}
                          onClick={() => model.deleteHook(hook.id)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : model.loading || model.loadError ? null : (
          <p className="text-[12px] leading-5 text-ha-muted">
            还没有 hook:创建后,对应事件发生时服务端会 POST 到你的 HTTPS 接收端。
          </p>
        )}

        <form
          className="rounded-md border border-ha-border bg-ha-panel-2 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            model.submit();
          }}
        >
          <p className="text-[12px] font-medium text-ha-text-2">新建 hook</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="事件" htmlFor="hook-event">
              <Select
                id="hook-event"
                value={model.form.event}
                onChange={(event) =>
                  model.updateForm({ event: event.target.value as HookEvent })
                }
              >
                {HOOK_EVENTS.map((event) => (
                  <option key={event} value={event}>
                    {EVENT_LABELS[event]}（{event}）
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="匹配工具(仅工具事件生效)" htmlFor="hook-matcher">
              <Input
                id="hook-matcher"
                value={model.form.matcher}
                placeholder="*"
                onChange={(event) =>
                  model.updateForm({ matcher: event.target.value })
                }
              />
            </Field>
            <Field label="目标地址(HTTPS)" htmlFor="hook-target">
              <Input
                id="hook-target"
                value={model.form.target}
                placeholder="https://example.com/dsh/hook"
                onChange={(event) =>
                  model.updateForm({ target: event.target.value })
                }
              />
            </Field>
            <Field label="超时(秒)" htmlFor="hook-timeout">
              <Input
                id="hook-timeout"
                type="number"
                min={HOOK_TIMEOUT_MIN}
                max={HOOK_TIMEOUT_MAX}
                value={model.form.timeout}
                onChange={(event) =>
                  model.updateForm({ timeout: event.target.value })
                }
              />
            </Field>
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] leading-5 text-ha-faint">
              只允许公网 HTTPS 地址;保存后 secret 只显示一次。
            </p>
            <Button
              type="submit"
              variant="outline"
              size="sm"
              disabled={model.createPending}
            >
              {model.createPending ? '创建中…' : '创建 hook'}
            </Button>
          </div>
          {model.validationError ? (
            <p role="alert" className="mt-2 text-[12px] text-ha-danger">
              {model.validationError}
            </p>
          ) : null}
        </form>

        <HookSecretDialog
          secret={model.secret}
          copied={model.copied}
          onCopy={model.copySecret}
          onClose={model.dismissSecret}
        />
      </CardContent>
    </Card>
  );
}
