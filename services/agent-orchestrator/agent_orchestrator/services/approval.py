"""审批策略引擎(P0-4/P1-A):工具调用要不要人工确认,由规则决定。

判据收口成两个纯函数:

- :func:`match_rule`:在候选规则里取**命中参数条件**的那一条(exact/prefix/glob);
- :func:`decide`::func:`match_rule` 的结论 + 风险等级默认值 → 最终动作。

规则来源有两类,统一成同一种行形状(`tool`/`action`/`arg_name`/`arg_match`/
`arg_value`/`origin`)喂给纯函数:

- **账号策略**(`origin="account"`):PUT /approval-policies 维护的整表规则;
- **会话授权**(`origin="grant"`):对话里用户选"本会话内信任"后落的 grant,
  只对同会话同名工具生效(动作恒为 auto)。

选择顺序(取第一条):

1. 工具名精确匹配优先;没有精确行才用通配 `*` 行;
2. 有参数规则命中时,参数规则优先于无条件规则;
3. 同档内 grant 行优先于账号策略行;
4. 同档同来源,动作严格度 deny > confirm > auto。

全都不命中才回落到**风险等级默认值**(DANGER → confirm,READ/WRITE → auto),
与引入策略表之前的行为完全一致。

命中 `deny` 时不执行工具,把拒绝原因作为工具结果回喂模型——它能看懂"这个动作
被策略禁止",然后换个做法,而不是让整个 run 失败。
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.approval import ApprovalPolicy
from agent_orchestrator.repositories import (
    ApprovalPolicyRepository,
    ConversationApprovalGrantRepository,
)
from agent_orchestrator.services.tools import ToolRisk

logger = get_logger(__name__)

#: 通配规则:匹配任意工具名。
WILDCARD = "*"

#: 规则动作(P0-4):自动放行 / 人工确认 / 直接拒绝。
VALID_ACTIONS = ("auto", "confirm", "deny")

#: 参数匹配方式:全等 / 前缀 / 通配(大小写敏感)。
VALID_MATCHES = ("exact", "prefix", "glob")

#: 规则来源:账号策略表 / 会话授权表。
ORIGIN_ACCOUNT = "account"
ORIGIN_GRANT = "grant"

#: 对话/任务确认时用户可选的授权范围。
GRANT_ONCE = "once"
GRANT_SESSION = "session"
GRANT_ALWAYS = "always"
VALID_GRANTS = (GRANT_ONCE, GRANT_SESSION, GRANT_ALWAYS)

#: 同一档内动作严格度:deny 最严,confirm 次之,auto 最宽松。
_ACTION_RANK = {"deny": 0, "confirm": 1, "auto": 2}

#: 坏规则只警告一次(按 (tool, 原因) 去重),避免每次工具调用刷屏。
_WARNED_RULES: set[tuple[str, str]] = set()


class ApprovalAction(StrEnum):
    """策略求值结果。"""

    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


def _warn_once(key: tuple[str, str], message: str, **fields: Any) -> None:
    if key in _WARNED_RULES:
        return
    _WARNED_RULES.add(key)
    logger.warning(message, **fields)


def _valid_action(rule: Mapping[str, Any]) -> str | None:
    action = str(rule.get("action") or "").lower()
    return action if action in VALID_ACTIONS else None


def _is_parameter_rule(rule: Mapping[str, Any]) -> bool:
    return bool(str(rule.get("arg_name") or "").strip())


def _normalize_arguments(
    arguments: Mapping[str, Any] | str | None, *, tool: str
) -> Mapping[str, Any]:
    """把工具参数归一成映射:字符串按 JSON 解析,解析失败视为空映射。"""
    if isinstance(arguments, Mapping):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            _warn_once(
                (tool, "invalid_arguments"),
                "approval.invalid_arguments",
                tool=tool,
            )
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _argument_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _argument_matches(rule: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
    name = str(rule.get("arg_name") or "").strip()
    if not name or name not in arguments:
        return False
    match = str(rule.get("arg_match") or "").strip().lower()
    value = _argument_text(arguments[name])
    expected = str(rule.get("arg_value") or "")
    if match == "exact":
        return value == expected
    if match == "prefix":
        return value.startswith(expected)
    if match == "glob":
        return fnmatch.fnmatchcase(value, expected)
    _warn_once(
        (str(rule.get("tool") or ""), f"invalid_match:{match}"),
        "approval.invalid_arg_match",
        tool=str(rule.get("tool") or ""),
        arg_match=match,
    )
    return False


def _pick(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """按"grant 优先 + 动作严格度"取第一条。"""
    if not rows:
        return None

    def sort_key(rule: Mapping[str, Any]) -> tuple[int, int]:
        origin = str(rule.get("origin") or ORIGIN_ACCOUNT)
        action = _valid_action(rule) or ""
        return (
            0 if origin == ORIGIN_GRANT else 1,
            _ACTION_RANK.get(action, len(_ACTION_RANK)),
        )

    return min(rows, key=sort_key)


def match_rule(
    policies: Sequence[Mapping[str, str]] | None,
    tool: str,
    arguments: Mapping[str, Any] | str | None = None,
) -> Mapping[str, str] | None:
    """取本次调用命中的那一条规则(纯函数,可单测);未命中返回 `None`。"""
    rows = [dict(item) for item in (policies or []) if item]
    # 动作非法的坏规则视为"没有这条规则",由 decide 回落到风险默认值。
    rows = [item for item in rows if _valid_action(item) is not None]
    if not rows:
        return None
    exact = [item for item in rows if str(item.get("tool") or "") == tool]
    candidates = exact or [
        item for item in rows if str(item.get("tool") or "") == WILDCARD
    ]
    if not candidates:
        return None
    normalized = _normalize_arguments(arguments, tool=tool)
    parameter_rows = [item for item in candidates if _is_parameter_rule(item)]
    if parameter_rows:
        matched = [
            item for item in parameter_rows if _argument_matches(item, normalized)
        ]
        if matched:
            return _pick(matched)
        # 有参数规则但都不匹配:退到无条件规则;没有无条件规则就是未命中。
        return _pick([item for item in candidates if not _is_parameter_rule(item)])
    return _pick(candidates)


def _risk_is_danger(risk: object) -> bool:
    return str(getattr(risk, "value", risk)).lower() == "danger"


def decide(
    policies: Sequence[Mapping[str, str]] | None,
    tool: str,
    risk: ToolRisk,
    arguments: Mapping[str, Any] | str | None = None,
) -> ApprovalAction:
    """求一个工具本次调用的审批动作(纯函数,可单测)。"""
    rule = match_rule(policies, tool, arguments)
    if rule is not None:
        action = _valid_action(rule)
        if action is not None:
            return ApprovalAction(action)
    return ApprovalAction.CONFIRM if _risk_is_danger(risk) else ApprovalAction.AUTO


async def load_policies(
    session: AsyncSession, account_id: str, conversation_id: str | None = None
) -> list[dict[str, str]]:
    """读策略快照(进图状态,节点不再碰数据库)。

    账号策略行在前(按唯一键排序),该会话的 grant 行在后(按工具名排序),
    每行统一带 `origin` 供 :func:`match_rule` 判断来源优先级。
    """
    rows = await ApprovalPolicyRepository(session).list_scoped(
        account_id,
        order_by=[
            ApprovalPolicy.tool,
            ApprovalPolicy.arg_name,
            ApprovalPolicy.arg_match,
            ApprovalPolicy.arg_value,
        ],
    )
    items: list[dict[str, str]] = [
        {
            "tool": row.tool,
            "action": row.action,
            "arg_name": row.arg_name or "",
            "arg_match": row.arg_match or "",
            "arg_value": row.arg_value or "",
            "origin": ORIGIN_ACCOUNT,
        }
        for row in rows
    ]
    if conversation_id:
        grants = await ConversationApprovalGrantRepository(
            session
        ).list_for_conversation(account_id, conversation_id)
        items.extend(
            {
                "tool": grant.tool,
                "action": ApprovalAction.AUTO.value,
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
                "origin": ORIGIN_GRANT,
            }
            for grant in grants
        )
    return items


async def replace_policies(
    session: AsyncSession, account_id: str, items: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]]:
    """整表替换该账号的策略(策略是"当前意图",不做逐条增量)。

    只替换账号策略行;会话授权表不参与 PUT(它由确认流落库、删会话时清理)。
    """
    repo = ApprovalPolicyRepository(session)
    await repo.delete_all_for_account(account_id)
    for item in items:
        session.add(
            ApprovalPolicy(
                account_id=account_id,
                tool=str(item["tool"]),
                action=str(item["action"]),
                arg_name=str(item.get("arg_name") or ""),
                arg_match=str(item.get("arg_match") or ""),
                arg_value=str(item.get("arg_value") or ""),
            )
        )
    await session.flush()
    return await load_policies(session, account_id)


async def record_grant(
    session: AsyncSession,
    *,
    account_id: str,
    conversation_id: str | None,
    tool: str,
    grant: str,
) -> str:
    """按用户选择落授权,返回实际生效的授权(`once` 表示没有落库)。

    - `session`:同会话同名工具以后自动放行;对话缺失时降级为 `once`;
    - `always`:写一条账号级 `auto` 策略(该工具以后都自动放行);
    - `once`/未知值:不落库。

    落库不提交,与调用方(确认结果回填)在同一事务里。
    """
    normalized = str(grant or GRANT_ONCE).strip().lower()
    if normalized == GRANT_SESSION:
        if not conversation_id:
            logger.warning(
                "approval.grant_without_conversation",
                grant=normalized,
                tool=tool,
                account_id=account_id,
            )
            return GRANT_ONCE
        await ConversationApprovalGrantRepository(session).upsert(
            account_id, conversation_id, tool
        )
        logger.info(
            "approval.grant",
            grant=normalized,
            tool=tool,
            origin=ORIGIN_GRANT,
            account_id=account_id,
            conversation_id=conversation_id,
        )
        return GRANT_SESSION
    if normalized == GRANT_ALWAYS:
        await ApprovalPolicyRepository(session).upsert_auto(account_id, tool)
        logger.info(
            "approval.grant",
            grant=normalized,
            tool=tool,
            origin=ORIGIN_ACCOUNT,
            account_id=account_id,
        )
        return GRANT_ALWAYS
    return GRANT_ONCE
