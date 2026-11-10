# 工具策略引擎（P1-A）：参数级规则与一次性/会话/长期授权

状态：2026-11-10 设计定稿，开始实现。
上游缺口：docs/gap-2026-11/00-refresh-2026-11-09.md §5.1（P1-A：工具 policy 引擎）与 §5.2（P1-B 共用决策点）。
相关：docs/design/tool-execution.md（exec 默认关、命令前缀 allowlist 的落点）、docs/design/code-execution.md（run_code 静态审查的前置）、docs/design/tool-service.md（工具的服务端风险下限）。

## 0. 一页结论

1. 现有审批策略（P0-4，services/approval.py）只按"工具名 + 风险等级"求值，无法表达"只允许 git status 这类命令"或"只允许写 docs/ 目录"。
2. 本次把规则扩成参数级：规则可带 arg_name / arg_match（exact | prefix | glob）/ arg_value，求值时用工具调用的 arguments 匹配。
3. 同时把"确认一次"升级为三种授权：本次（once）/ 本会话（session）/ 长期（always）。
   - once：保持现状，只放行这一个调用，不落任何规则；
   - session：写一张 conversation_approval_grants，本会话后续同名工具不再确认；
   - always：写一条账号级 approval_policies（tool + action=auto），所有新 run/会话生效。
4. 任务路径与对话路径共用同一个纯函数 match_rule / decide，只是对 CONFIRM 的处置不同（任务有确认闸，对话写操作走就地确认卡片）。
5. 不做的事：不做 shell 语法解析（前缀匹配不是安全边界；exec 默认仍关闭）、不按 run 维度授权、不给 grant 单独做管理页（删除会话即回收 session grant）。

## 1. 数据模型

### 1.1 approval_policies（扩列）

沿用表，新增三列，全部 String 非空、server_default 为空串（不用 NULL：唯一约束在 PG/SQLite 对 NULL 的语义不一致）：

| 列        | 类型                  | 说明                                              |
| --------- | --------------------- | ------------------------------------------------- |
| arg_name  | String(64)，默认空串  | 参数名，如 command、path、url；空串表示不限定参数 |
| arg_match | String(16)，默认空串  | exact / prefix / glob；arg_name 非空时必填        |
| arg_value | String(512)，默认空串 | 比较值；arg_name 非空时必填（prefix 也要求非空）  |

唯一约束从 uq_approval_policies_account_tool (account_id, tool) 改为
uq_approval_policies_account_tool_args (account_id, tool, arg_name, arg_match, arg_value)。

迁移（AO）：加列 + 删旧唯一约束 + 建新唯一约束；存量行三列取默认空串，因此旧"一工具一条"的约束仍被新约束自然保持。

### 1.2 conversation_approval_grants（新表）

| 列              | 类型                       | 说明                |
| --------------- | -------------------------- | ------------------- |
| 主键/时间       | UUIDMixin / TimestampMixin | 与其它模型一致      |
| account_id      | AccountMixin               | 账号隔离            |
| conversation_id | String(36)                 | 会话                |
| tool            | String(120)                | 工具名（不支持 \*） |

唯一约束 uq_conversation_grants_account_conversation_tool (account_id, conversation_id, tool)。
语义：action 恒为 auto，不设额外列。删除会话时同事务删除该会话全部 grant（services/conversations_queries.py:225 delete_conversation）。
不做 TTL：会话生命周期即授权生命周期。

## 2. 规则匹配（纯函数，不查库）

新函数，services/approval.py：

    def match_rule(
        policies: Sequence[Mapping[str, str]] | None,
        tool: str,
        arguments: Mapping[str, Any] | str | None = None,
    ) -> Mapping[str, str] | None: ...

    def decide(
        policies: Sequence[Mapping[str, str]] | None,
        tool: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any] | str | None = None,
    ) -> ApprovalAction: ...

decide 改为在 match_rule 命中时返回该行动作，未命中才回落到风险默认（DANGER→confirm，其余 auto）。行为兼容：旧的两参数规则与旧调用方式结果不变。

### 2.1 候选集

- 先取 tool 精确相等的行；没有精确行时才取 tool == "\*" 的行（通配仅作兜底，和现状一致；通配行参与参数匹配）。
- arguments 归一：Mapping 直接用；字符串先 json.loads；解析失败/None 视为空映射（此时任何带参数条件的规则都不匹配）。

### 2.2 优先级

对候选行排序后取第一条：

1. 参数规则优先于无条件规则：arg_name 非空且参数匹配的行，优先于 arg_name 为空的行；
2. 同一档内，grant 行优先于账号策略行（grant 是用户刚刚在确认卡片上明确点的，必须能覆盖账号级 confirm）；
3. 同一档同一来源内，取更严格的动作：deny > confirm > auto。

参数匹配：

- arg_name 在 arguments 里不存在 → 不匹配；
- 取到的值转字符串（非字符串参数用 json.dumps(ensure_ascii=False)）；
- exact 全等；prefix 用 str.startswith；glob 用 fnmatch.fnmatchcase（大小写敏感）；
- 任一非法 arg_match 或解析异常的行按"不匹配"处理并 logger.warning（不让坏数据打断执行）。

### 2.3 例子

| 规则                                     | 调用                             | 结果                                  |
| ---------------------------------------- | -------------------------------- | ------------------------------------- |
| exec / confirm / 无参数                  | exec {"command":"rm -rf /"}      | confirm（工具级兜底）                 |
| exec / auto / command prefix git status  | exec {"command":"git status -s"} | auto（参数规则更具体）                |
| 同上                                     | exec {"command":"rm -rf /"}      | confirm（参数规则不匹配，回到工具级） |
| write_file / auto / path glob docs/\*\*  | write_file {"path":"docs/a.md"}  | auto                                  |
| 账号 tool confirm + 会话 grant tool auto | 该工具                           | auto（grant 优先）                    |

## 3. 账号策略读取与写入

load_policies(session, account_id, conversation_id=None) 返回账号策略 + 该会话 grant 的统一行结构（每行至少含 tool/action/arg_name/arg_match/arg_value/origin，origin 取 account 或 grant）。账号行在前（按 tool 排序），grant 行在后（按 tool 排序）。

replace_policies(session, account_id, items) 仍是整表替换，只替换 origin=account 的行；grant 表不参与 PUT。

调用点：

- run 路径 services/run_execution_invoke.py:151 → load_policies(session, account_id, run.conversation_id)；
- chat 路径 services/conversations_messages.py:170 与 :395 → 传 conversation_id。

## 4. 两条路径的决策行为（必须一致）

| 决策                          | 任务（graph/nodes/execute.py） | 对话（services/chat.py）                                                   |
| ----------------------------- | ------------------------------ | -------------------------------------------------------------------------- |
| deny                          | 不执行，回喂"被策略禁止"       | 同左                                                                       |
| confirm（显式或 DANGER 默认） | 收进 pending_writes 进确认闸   | DANGER：回喂"危险操作请改用任务"；WRITE：落 pending 确认卡片               |
| auto 且命中规则               | 执行 approved=True             | 执行；risk=write 且命中显式 auto 时 approved=True（这是 grant 生效的关键） |
| auto 未命中规则（风险默认）   | 执行 approved=True             | READ 执行 approved=False；WRITE 仍走就地确认（与现状一致）                 |

对话路径要点：\_execute_calls 在写工具分支前先用 match_rule 判断是否命中显式 auto；命中则直接执行 approved=True，不落确认卡片。这样 once/session/always 之后同名写工具不再重复打断。

## 5. 授权（once / session / always）

### 5.1 对话确认端点

POST /v1/conversations/{conversation_id}/tool-confirmations/{confirmation_id}/confirm

请求体新可选（不传等价 once，旧客户端不受影响）：

    { "grant": "once" | "session" | "always" }

- once：不落规则；
- session：确认成功后 upsert conversation_approval_grants（同会话同名工具幂等）；
- always：确认成功后 upsert approval_policies 的 (tool, arg_name="", arg_match="", arg_value="") 行、action=auto；
- 参数非法 → 422（schemas/conversations.py 的请求模型做 Literal 校验）。

### 5.2 run 确认端点

POST /v1/runs/{run_id}/confirm 的 ConfirmRequest 加同名字段 grant: Literal["once","session","always"] = "once"。

- 只在 action="approve" 且 grant != "once" 时落授权；reject 忽略 grant（不报错，保持幂等）；
- 授权对象是本次确认闸里的 pending_writes 的工具名集合（从 run state / 事件载荷取；取不到就什么都不写，日志 warning）；
- run.conversation_id 为空时 session 降级为 once（日志说明），always 仍写账号策略；
- 落库发生在状态推进之后、同一事务内（失败随请求回滚）。

### 5.3 语义边界（文档化给用户）

- session 只覆盖同名工具的后续调用；已生成 pending 的确认仍按原记录执行。
- always 是账号级规则，会出现在设置页的策略表里，可被整表替换（PUT）移除。
- 授权不改变工具服务端的最低风险校验（tool-service 的 approved 凭据与风险下限仍是最后一道）。

## 6. API 契约

### 6.1 GET /v1/approval-policies

    [
      {"tool": "exec", "action": "auto", "arg_name": "command", "arg_match": "prefix", "arg_value": "git status"},
      {"tool": "*", "action": "confirm", "arg_name": "", "arg_match": "", "arg_value": ""}
    ]

### 6.2 PUT /v1/approval-policies

    {
      "policies": [
        {"tool": "exec", "action": "auto", "arg_name": "command", "arg_match": "prefix", "arg_value": "git status"},
        {"tool": "write_file", "action": "confirm"}
      ]
    }

校验：

- tool 非空、去首尾空白，长度 ≤120；action 取 auto/confirm/deny；
- arg_name 去空白后为空 ⇒ 三列必须都缺省/空；arg_name 非空 ⇒ arg_match 取 exact/prefix/glob 且 arg_value 非空；
- arg_name ≤64、arg_value ≤512；
- 唯一键 (tool, arg_name, arg_match, arg_value) 不得重复 → 422（Pydantic 校验错误，沿用现有错误契约）。

### 6.3 确认请求

- 对话：见 §5.1；
- run：{"action":"approve","reason":null,"grant":"session"}。

新增/变化的错误码：无新增业务错误码（非法 grant 走 422 参数校验）；确认端点原有 404/409 契约不变。

## 7. 前端契约

### 7.1 设置页「工具审批策略」面板

apps/web/src/features/settings/approval-policy-panel.tsx + use-approval-policy-model.ts + src/lib/approval-api.ts：

- 表格列：工具、参数条件（arg_name arg_match arg_value 拼一行；空为"—"）、动作（auto/confirm/deny 用 ToneBadge 区分）、删除；
- 新增行：工具名输入 + 动作下拉 + 可选参数条件（参数名、匹配方式下拉、值）；
- 保存语义 = PUT 整表；保存成功刷新；失败展示错误（不静默）；
- 空表说明"默认按风险等级：只读/写自动，危险操作需确认"；
- 放在设置页 SettingsPage 的 AccountCard 之后，标题「工具审批策略」，并注明 always 授权会出现在这里。

### 7.2 确认卡片三选项

- 对话写确认卡（tool-confirmation-card.tsx）：「确认执行」主按钮旁增加授权选择（默认本次）；选「本会话」/「始终」后调用 confirmToolCall(conversationId, confirmationId, grant)，并提示该工具后续不再确认；取消语义不变。
- run 危险确认（run-detail-actions.tsx 与聊天内 DangerConfirmCard）：确认按钮旁同样给三档；runsApi.confirm(runId, 'approve', undefined, grant)。文案：「本次允许」「本会话允许这些工具」「始终允许这些工具」。
- 移动端/窄屏：三档用下拉或分段控件，不做折叠菜单。

### 7.3 客户端签名

    conversationsApi.confirmToolCall(conversationId, confirmationId, grant?: 'once'|'session'|'always')
    runsApi.confirm(runId, action, reason?, grant?: 'once'|'session'|'always')
    approvalApi.list(): Promise<ApprovalPolicy[]>
    approvalApi.replace(policies: ApprovalPolicyInput[]): Promise<ApprovalPolicy[]>

## 8. 观测

- 规则求值不额外打点（在 graph 节点内是纯函数）；确认授权落库时 logger.info("approval.grant", extra={...})：grant、tool、origin、account_id、conversation_id?。
- 坏规则行（非法 arg_match / 解析失败）logger.warning 一次，带 tool 与 arg_match，不抛。
- 确认响应中的现有事件（run.confirmation.received.v1）载荷不变（仍只有 action/reason），避免事件契约漂移；grant 只影响后续决策。

## 9. 安全边界与残余

- prefix/glob 只是策略便利，不是命令注入防护：exec 仍默认关闭，开启后建议只对可信命令配置 auto；真正的执行隔离在沙箱与 tool-service 风险下限。
- always 允许 danger 工具在账号内免确认（这是显式授权）；不做二次确认、不做有效期。
- 规则冲突解释成本：同一工具多条参数规则时可能"最严格者胜"，设置页不提示冲突（后续可加 lint）。
- run 的策略快照在进图时加载；run 中途产生的 grant/always 只影响新 run（同一个 run 的后续调用仍用旧快照）。文档化，不做热更新。
- grant 没有独立管理入口与审计事件（删除会话即回收；always 在策略面板可删）。

## 10. 测试清单（后端）

- tests/test_approval_policy.py：参数匹配 exact/prefix/glob、参数缺失、坏 JSON、非法 arg_match、参数规则优先、grant 优先、deny>confirm>auto、通配兜底、旧两参数规则兼容、load 带 conversation grant、replace 不动 grant。
- tests/test_tool_confirmations.py：confirm 带 grant=session 落 grant 且后续同工具 auto；grant=always 写 policy；grant=once 不落库；幂等重复确认只落一次。
- tests/test_runs\*.py / confirm 路径：approve+grant 落库；reject 忽略；无 conversation 的 session 降级；pending_writes 为空不报错。
- tests/test_approval_policies_api.py：PUT 扩展字段往返、唯一键 422、无参数行约束、GET 形状。
- chat 决策：写工具命中显式 auto 时执行 approved=True 且不再落 pending。

## 11. 实施拆分

- 后端（P1-A）：services/approval.py、models/approval.py、repositories.py（ApprovalPolicyRepository upsert + ConversationApprovalGrantRepository）、schemas/approval.py、api/v1/approval_policies.py、services/tool_confirmations.py、api/v1/tool_confirmations.py、services/run_execution_approvals.py（confirm_run）、api/v1/run_actions.py、graph/nodes/execute.py（传 arguments）、services/chat.py（显式 auto 放行）、services/conversations\*.py（load 传 conversation_id、删除会话清 grant）、迁移、测试。
- 前端：lib/approval-api.ts、features/settings/approval-policy-panel.tsx + model + 测试、features/chat/tool-confirmation-card.tsx、features/runs/run-detail-actions.tsx、approval-block.tsx、lib/runs-api.ts、lib/conversations-api.ts + 相关测试。
- 文档：本文件、docs/README.md 索引、docs/gap-2026-11 §2/§3/§5.1/§7、docs/roadmap.md。
