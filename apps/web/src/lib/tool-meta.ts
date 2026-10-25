/**
 * 工具调用的可选富字段(P1 受控写起由后端补充)。
 *
 * 事件 payload 与 SSE tool 帧都沿用后端的 snake_case 字段名,这里集中做一次归一化:
 * - 字段缺省(旧数据/旧后端)一律不产出键,渲染层据此优雅降级;
 * - risk 收窄成 read | write | danger(destructive 落到 danger);
 * - decision 收窄成 auto | approved(confirmed 落到 approved);
 * - duration_ms / result_chars 兼容数字字符串,非法值当作缺省。
 */
export type ToolRisk = 'read' | 'write' | 'danger';

export type ToolDecision = 'auto' | 'approved';

/** 前端展示用的富字段(全部可缺省)。 */
export type ToolCallMeta = {
  toolCallId?: string;
  risk?: ToolRisk;
  decision?: ToolDecision;
  errorCode?: string;
  durationMs?: number;
  resultChars?: number;
  truncated?: boolean;
  paths?: string[];
  sha256Before?: string;
  sha256After?: string;
};

const RISK_BY_FIELD: Record<string, ToolRisk> = {
  read: 'read',
  write: 'write',
  danger: 'danger',
  destructive: 'danger',
};

const DECISION_BY_FIELD: Record<string, ToolDecision> = {
  auto: 'auto',
  approved: 'approved',
  confirmed: 'approved',
};

function asText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function asCount(value: unknown): number | undefined {
  const numeric =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim()
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : undefined;
}

function asPaths(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const paths = value.filter(
    (item): item is string => typeof item === 'string' && item.trim() !== '',
  );
  return paths.length > 0 ? paths : undefined;
}

/**
 * 从后端字段对象(run 事件 payload / SSE tool 帧 data)提取富字段。
 *
 * 只认白名单键;缺省或非法值不产出键,调用方可以整体 spread 进展示对象,
 * 旧数据不会多出 undefined 字段。
 */
export function toolCallMetaFrom(
  source: Record<string, unknown>,
): ToolCallMeta {
  const meta: ToolCallMeta = {};
  const toolCallId = asText(source.tool_call_id);
  if (toolCallId) meta.toolCallId = toolCallId;
  const risk =
    typeof source.risk === 'string' ? RISK_BY_FIELD[source.risk] : undefined;
  if (risk) meta.risk = risk;
  const decision =
    typeof source.decision === 'string'
      ? DECISION_BY_FIELD[source.decision]
      : undefined;
  if (decision) meta.decision = decision;
  const errorCode = asText(source.error_code);
  if (errorCode) meta.errorCode = errorCode;
  const durationMs = asCount(source.duration_ms);
  if (durationMs !== undefined) meta.durationMs = durationMs;
  const resultChars = asCount(source.result_chars);
  if (resultChars !== undefined) meta.resultChars = resultChars;
  if (source.truncated === true) meta.truncated = true;
  const paths = asPaths(source.paths);
  if (paths) meta.paths = paths;
  const sha256Before = asText(source.sha256_before);
  if (sha256Before) meta.sha256Before = sha256Before;
  const sha256After = asText(source.sha256_after);
  if (sha256After) meta.sha256After = sha256After;
  return meta;
}
