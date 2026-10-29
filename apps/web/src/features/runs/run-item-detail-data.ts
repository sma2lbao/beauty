/**
 * 运行项展开详情的展示数据:把柔性的 data 归一成「两列字段行 + 长文本块」。
 * 纯函数、不依赖 React;视图见 ./run-item-detail.tsx。
 */
import { RUN_STATUS_LABELS, formatTokens } from '@/lib/presentation';
import type { RunItem } from '@/lib/runs-api';

import { booleanOf, numberOf, subagentOf, textOf } from './run-item-meta';

/* ── 结构化 data 的展示映射 ───────────────────────────────────────────── */

export type DetailRow = { label: string; value: string };
export type DetailBlock = { label: string; content: string };

/** 标量字段拼成两列 dl;长文本(arguments/detail/content)单独成块。 */
export function detailRows(item: RunItem): DetailRow[] {
  const data = item.data;
  const rows: DetailRow[] = [];
  const push = (label: string, value: string | null) => {
    if (value) rows.push({ label, value });
  };
  const ok = booleanOf(data.ok);
  push('名称', textOf(data.name));
  push('状态', ok === null ? null : ok ? '成功' : '失败');
  push('风险', textOf(data.risk));
  push('决策', textOf(data.decision));
  push('错误码', textOf(data.error_code));
  push('结果字符', numberOf(data.result_chars)?.toString() ?? null);
  push(
    '截断',
    booleanOf(data.truncated) === null
      ? null
      : booleanOf(data.truncated)
        ? '已截断'
        : '完整',
  );
  push('工具调用', textOf(data.tool_call_id));
  push('MCP server', textOf(data.server));
  push('MCP tool', textOf(data.tool));
  if (item.kind === 'subagent') {
    const subagent = subagentOf(item);
    push('代理', subagent?.agentName ?? null);
    push('子 run', subagent?.childRunId ?? null);
    push(
      '子 run 状态',
      subagent?.childStatus ? RUN_STATUS_LABELS[subagent.childStatus] : null,
    );
    push(
      '超时',
      subagent?.timeoutSeconds === null ||
        subagent?.timeoutSeconds === undefined
        ? null
        : `${subagent.timeoutSeconds}s`,
    );
    if (subagent?.usage) {
      push('调用', `${subagent.usage.calls} 次`);
      push('tokens', formatTokens(subagent.usage.totalTokens));
    }
    push(
      '工具',
      subagent && subagent.tools.length > 0 ? subagent.tools.join('、') : null,
    );
  }
  if (item.kind === 'plan') {
    push('版本', numberOf(data.version)?.toString() ?? null);
    push('字符', numberOf(data.chars)?.toString() ?? null);
    push('替代版本', textOf(data.supersedes));
  }
  if (item.node) push('节点', item.node);
  if (item.step_id) push('步骤', item.step_id);
  return rows;
}

/** 长文本块:工具参数/结果、检索参数按原样(可滚动)展示。 */
export function detailBlocks(item: RunItem): DetailBlock[] {
  const argumentsText = textOf(item.data.arguments);
  const resultText =
    textOf(item.data.detail) ??
    textOf(item.data.output) ??
    textOf(item.data.result);
  const content = textOf(item.data.content);
  const blocks: DetailBlock[] = [];
  if (item.kind === 'plan' && content) {
    blocks.push({ label: '正文', content });
  }
  if (argumentsText) blocks.push({ label: '参数', content: argumentsText });
  if (resultText) blocks.push({ label: '结果', content: resultText });
  return blocks;
}
