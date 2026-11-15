"""P0-4 审批策略引擎:纯函数求值(风险默认值、精确/通配规则、参数级匹配)

断言强度与拆分前一致;端点契约见 test_approval_policy_endpoints.py,
run/对话里的实际生效见 test_approval_policy_effects.py,
服务层 grant 合并见 test_approval_policy_grants.py。
"""

from __future__ import annotations

from agent_orchestrator.services.approval import ApprovalAction, decide, match_rule
from agent_orchestrator.services.tools import ToolRisk

# ---- 纯函数:策略求值 ----


def test_defaults_follow_risk_when_no_policy() -> None:
    assert decide([], "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO
    assert decide([], "workspace_write_file", ToolRisk.WRITE) is ApprovalAction.AUTO
    assert decide([], "external_publish", ToolRisk.DANGER) is ApprovalAction.CONFIRM
    assert decide(None, "external_publish", ToolRisk.DANGER) is ApprovalAction.CONFIRM


def test_exact_tool_policy_overrides_default() -> None:
    policies = [
        {"tool": "external_publish", "action": "auto"},
        {"tool": "workspace_write_file", "action": "confirm"},
    ]
    assert decide(policies, "external_publish", ToolRisk.DANGER) is ApprovalAction.AUTO
    assert (
        decide(policies, "workspace_write_file", ToolRisk.WRITE)
        is ApprovalAction.CONFIRM
    )
    # 未命中的工具仍走风险默认值
    assert decide(policies, "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO


def test_wildcard_applies_when_no_exact_match() -> None:
    policies = [
        {"tool": "*", "action": "deny"},
        {"tool": "read_pdf", "action": "auto"},
    ]
    assert decide(policies, "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO
    assert decide(policies, "search_knowledge", ToolRisk.READ) is ApprovalAction.DENY
    # 精确规则优先于通配,即使风险更高
    assert decide(policies, "external_publish", ToolRisk.DANGER) is ApprovalAction.DENY


def test_unknown_action_falls_back_to_risk_default() -> None:
    assert (
        decide(
            [{"tool": "external_publish", "action": "maybe"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.CONFIRM
    )


# ---- 纯函数:参数级规则(§6.1) ----


def test_match_rule_matches_arguments_by_mode() -> None:
    policies = [
        {
            "tool": "deploy",
            "action": "auto",
            "arg_name": "env",
            "arg_match": "exact",
            "arg_value": "staging",
        },
        {
            "tool": "deploy",
            "action": "confirm",
            "arg_name": "env",
            "arg_match": "prefix",
            "arg_value": "prod",
        },
        {
            "tool": "deploy",
            "action": "deny",
            "arg_name": "path",
            "arg_match": "glob",
            "arg_value": "*.pem",
        },
    ]
    assert match_rule(policies, "deploy", {"env": "staging"})["action"] == "auto"
    assert match_rule(policies, "deploy", {"env": "production"})["action"] == "confirm"
    assert match_rule(policies, "deploy", {"path": "secret.pem"})["action"] == "deny"
    # 参数缺失 / 值不命中 → 未命中
    assert match_rule(policies, "deploy", {"env": "dev"}) is None
    assert match_rule(policies, "deploy", None) is None


def test_match_rule_parameter_rule_beats_tool_default() -> None:
    policies = [
        {"tool": "publish", "action": "deny"},
        {
            "tool": "publish",
            "action": "auto",
            "arg_name": "title",
            "arg_match": "prefix",
            "arg_value": "周报",
        },
    ]
    assert (
        match_rule(policies, "publish", {"title": "周报 2026-W45"})["action"] == "auto"
    )
    # 参数不命中:退到同工具的无条件规则
    assert match_rule(policies, "publish", {"title": "别的"})["action"] == "deny"
    assert decide(policies, "publish", ToolRisk.WRITE) is ApprovalAction.DENY


def test_match_rule_prefers_grant_then_severity() -> None:
    grant = {
        "tool": "notes__create",
        "action": "auto",
        "arg_name": "",
        "origin": "grant",
    }
    account_confirm = {
        "tool": "notes__create",
        "action": "confirm",
        "origin": "account",
    }
    # 同档内 grant 行优先于账号策略行
    assert match_rule([account_confirm, grant], "notes__create")["origin"] == "grant"
    assert (
        decide([account_confirm, grant], "notes__create", ToolRisk.WRITE)
        is ApprovalAction.AUTO
    )
    # 同档同来源:deny > confirm > auto
    rows = [
        {"tool": "t", "action": "auto", "origin": "account"},
        {"tool": "t", "action": "deny", "origin": "account"},
        {"tool": "t", "action": "confirm", "origin": "account"},
    ]
    assert decide(rows, "t", ToolRisk.READ) is ApprovalAction.DENY
    # 参数规则优先于无条件规则
    param = {
        "tool": "t",
        "action": "auto",
        "arg_name": "x",
        "arg_match": "exact",
        "arg_value": "1",
        "origin": "account",
    }
    assert decide([*rows, param], "t", ToolRisk.READ, {"x": "1"}) is ApprovalAction.AUTO


def test_match_rule_wildcard_and_legacy_rows() -> None:
    policies = [
        {"tool": "*", "action": "deny"},
        {
            "tool": "*",
            "action": "auto",
            "arg_name": "dry_run",
            "arg_match": "exact",
            "arg_value": "true",
        },
    ]
    assert match_rule(policies, "anything", {"dry_run": True})["action"] == "auto"
    assert match_rule(policies, "anything", {})["action"] == "deny"
    # 旧两键行(无参数列)继续生效,DANGER 默认值也能被覆盖
    assert (
        decide(
            [{"tool": "external_publish", "action": "auto"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.AUTO
    )
    assert (
        decide(
            [{"tool": "external_publish", "action": "deny"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.DENY
    )


def test_match_rule_ignores_broken_arguments_and_match_mode() -> None:
    # 非法 arg_match:该参数规则不匹配,退到无条件规则(不抛异常)
    policies = [
        {
            "tool": "t",
            "action": "deny",
            "arg_name": "x",
            "arg_match": "regex",
            "arg_value": "1",
        },
        {"tool": "t", "action": "auto"},
    ]
    assert match_rule(policies, "t", {"x": "1"})["action"] == "auto"
    # 坏 JSON 参数:视为空映射,带参数条件的规则一律不匹配
    only_param = [
        {
            "tool": "t",
            "action": "deny",
            "arg_name": "x",
            "arg_match": "exact",
            "arg_value": "1",
        }
    ]
    assert match_rule(only_param, "t", "{not json") is None
    # 非字符串参数值按 JSON 文本比较
    numeric = [
        {
            "tool": "t",
            "action": "auto",
            "arg_name": "n",
            "arg_match": "exact",
            "arg_value": "1",
        }
    ]
    assert match_rule(numeric, "t", {"n": 1})["action"] == "auto"
