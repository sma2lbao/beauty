/**
 * 右列:写入记忆表单(内容/类型/作用域/关联工作区/有效期/置顶)。
 */
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Checkbox,
  Field,
  Input,
  Select,
  Textarea,
} from '@beauty/design';

import {
  MEMORY_KINDS,
  MEMORY_SCOPES,
  type MemoryKind,
  type MemoryScope,
} from '@/lib/memory-api';

import { KIND_LABELS, SCOPE_LABELS } from './display';
import type { MemoryModel } from './use-memory-model';

export function MemoryCreateForm({ model }: { model: MemoryModel }) {
  const {
    content,
    setContent,
    newKind,
    setNewKind,
    newScope,
    setNewScope,
    newWorkspaceId,
    setNewWorkspaceId,
    pinned,
    setPinned,
    ttlDays,
    setTtlDays,
    ttlError,
    canCreate,
    createMutation,
    workspaces,
  } = model;

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>写入记忆</CardTitle>
        <CardDescription>
          沉淀稳定的偏好/事实/流程;按语义检索,置顶条目始终注入上下文。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="记忆内容" htmlFor="memory-content" required>
          <Textarea
            id="memory-content"
            rows={4}
            placeholder="例如:回复默认使用中文,并先给结论。"
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="记忆类型" htmlFor="memory-kind-new">
            <Select
              id="memory-kind-new"
              value={newKind}
              onChange={(event) => setNewKind(event.target.value as MemoryKind)}
            >
              {MEMORY_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {KIND_LABELS[kind]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="作用域" htmlFor="memory-scope-new">
            <Select
              id="memory-scope-new"
              value={newScope}
              onChange={(event) =>
                setNewScope(event.target.value as MemoryScope)
              }
            >
              {MEMORY_SCOPES.map((scope) => (
                <option key={scope} value={scope}>
                  {SCOPE_LABELS[scope]}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {newScope === 'workspace' ? (
          <Field
            label="关联工作区"
            htmlFor="memory-workspace-new"
            hint="可留空:不绑定具体工作区。"
          >
            <Select
              id="memory-workspace-new"
              value={newWorkspaceId}
              onChange={(event) => setNewWorkspaceId(event.target.value)}
            >
              <option value="">不绑定</option>
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="有效期(天)"
            htmlFor="memory-ttl"
            hint="留空表示永久。"
            error={ttlError}
          >
            <Input
              id="memory-ttl"
              type="number"
              min={0}
              max={3650}
              placeholder="例如 30"
              value={ttlDays}
              onChange={(event) => setTtlDays(event.target.value)}
            />
          </Field>
          <div className="flex items-end pb-2">
            <Checkbox
              id="memory-pinned"
              checked={pinned}
              onCheckedChange={(checked) => setPinned(checked)}
              label="置顶(始终注入)"
            />
          </div>
        </div>

        <Button
          onClick={() => createMutation.mutate()}
          disabled={!canCreate || createMutation.isPending}
        >
          {createMutation.isPending ? '写入中…' : '写入记忆'}
        </Button>
      </CardContent>
    </Card>
  );
}
