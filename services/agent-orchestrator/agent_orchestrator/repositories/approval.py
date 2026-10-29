"""审批策略仓储(按域拆分)。"""

from __future__ import annotations

from database.repository import TenantRepository

from agent_orchestrator.models.approval import ApprovalPolicy


class ApprovalPolicyRepository(TenantRepository[ApprovalPolicy]):
    model = ApprovalPolicy
    not_found_code = "approval_policy.not_found"
    not_found_message = "审批策略不存在"
    id_detail_key = "policy_id"
