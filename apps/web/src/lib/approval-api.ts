/**
 * 工具审批策略数据层(P1-A):账号级规则整表读/写。
 *
 * 规则语义见 docs/design/tool-policy.md §2/§6:工具级行(arg_name 为空)是兜底,
 * 参数级行更具体、优先命中;PUT 提交的是更新后的**完整**策略集,
 * 所以「删除一行」也是整表 PUT。服务在 agent-orchestrator,沿用 /api/v1 前缀。
 */
import { createHttpClient } from '@/lib/http';
import { runsBase as BASE } from '@/config/env';

export type ApprovalAction = 'auto' | 'confirm' | 'deny';
export type ApprovalArgMatch = 'exact' | 'prefix' | 'glob';
/** 确认卡片的授权范围(§5):不传等价 once。 */
export type ApprovalGrant = 'once' | 'session' | 'always';

export type ApprovalPolicy = {
  tool: string;
  action: ApprovalAction;
  arg_name: string;
  arg_match: ApprovalArgMatch | '';
  arg_value: string;
};

export type ApprovalPolicyInput = {
  tool: string;
  action: ApprovalAction;
  arg_name?: string;
  arg_match?: ApprovalArgMatch;
  arg_value?: string;
};

const http = createHttpClient(BASE);

/** 旧数据/旧服务端可能不带参数三列:统一补空串,展示层不用再判 undefined。 */
function normalize(row: ApprovalPolicy): ApprovalPolicy {
  return {
    tool: row.tool,
    action: row.action,
    arg_name: row.arg_name ?? '',
    arg_match: row.arg_match ?? '',
    arg_value: row.arg_value ?? '',
  };
}

export const approvalApi = {
  list: async () =>
    (await http.request<ApprovalPolicy[]>('/api/v1/approval-policies')).map(
      normalize,
    ),
  replace: async (policies: ApprovalPolicyInput[]) =>
    (
      await http.request<ApprovalPolicy[]>('/api/v1/approval-policies', {
        method: 'PUT',
        body: JSON.stringify({ policies }),
      })
    ).map(normalize),
};
