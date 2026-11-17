"""外部副作用台账(S5):rewind/fork 之前必须知道"哪些写已经真的发生过"。

权威来源是 run.tool.called.v1 事件里的 risk(write/danger):写工具只要发起过就
计入台账,失败也算——超时/interrupt 的写同样改过外部状态且无法回滚。只读投影,
不新增表、不改迁移。

为什么需要:rewind 会从 checkpoint 重放之后的节点。同一参数、同一工具的重复调用
由稳定幂等键(S1)按"重放"返回原结果,但参数变了就是一次新的外部写(改需求恰恰
是这个场景),fork 换 run_id 后更是全新的键。所以回退不是无损操作,必须让发起方
先看到"已经写过什么",再显式确认(acknowledge_side_effects)。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.repositories.constants import TOOL_CALLED_EVENT
from agent_orchestrator.services.run_lifecycle import require_run

#: 会产生外部副作用的工具等级(READ 只读,不进台账)。
WRITE_RISKS = frozenset({"write", "danger"})

SIDE_EFFECT_CODE = "run.side_effects_present"
SIDE_EFFECT_MESSAGE = "该 run 已发生外部写操作,回退重跑可能重复执行,需要显式确认"

#: 409 响应里最多带多少条明细(前端可换成 GET /side-effects 看全量)。
MAX_DETAILS = 20
#: 单条参数的展示上限。
MAX_ARGUMENTS_CHARS = 200


@dataclass(frozen=True, slots=True)
class SideEffect:
    """一次已发起的外部写:事件 seq + 工具 + 风险 + 结果 + 参数预览。"""

    seq: int
    tool_call_id: str | None
    name: str
    risk: str
    decision: str
    ok: bool
    arguments: str

    def preview(self) -> dict[str, object]:
        """409 details 里的精简形态(不带参数全文,避免响应膨胀)。"""
        return {
            "seq": self.seq,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "risk": self.risk,
            "ok": self.ok,
        }


async def list_side_effects(
    session: AsyncSession, account_id: str, run_id: str
) -> list[SideEffect]:
    """该 run 已发起的外部写(按 seq 升序);读工具与展示事件一律不计。"""
    await require_run(session, account_id, run_id)
    events = await RunEventRepository(session).list_for_run(account_id, run_id)
    effects: list[SideEffect] = []
    for event in events:
        if event.type != TOOL_CALLED_EVENT:
            continue
        payload = event.payload or {}
        risk = str(payload.get("risk") or "")
        if risk not in WRITE_RISKS:
            continue
        effects.append(
            SideEffect(
                seq=event.seq,
                tool_call_id=payload.get("tool_call_id") or None,
                name=str(payload.get("name") or ""),
                risk=risk,
                decision=str(payload.get("decision") or "auto"),
                ok=bool(payload.get("ok")),
                arguments=str(payload.get("arguments") or "")[:MAX_ARGUMENTS_CHARS],
            )
        )
    return effects


def side_effect_summary(effects: list[SideEffect]) -> dict[str, object]:
    """事件 payload / 409 details 共用的汇总:数量 + 去重后的工具名。"""
    return {
        "count": len(effects),
        "tools": sorted({effect.name for effect in effects if effect.name}),
    }


async def require_acknowledged(
    session: AsyncSession,
    account_id: str,
    run_id: str,
    *,
    acknowledge: bool,
    action: str,
) -> list[SideEffect]:
    """rewind/fork 前的闸:已发生过外部写且未确认 → 409 带台账明细。

    返回台账本身,调用方把它写进事件 payload(审计"当时确认了哪几次写")。
    """
    effects = await list_side_effects(session, account_id, run_id)
    if effects and not acknowledge:
        raise ConflictError(
            SIDE_EFFECT_CODE,
            SIDE_EFFECT_MESSAGE,
            details={
                "run_id": run_id,
                "action": action,
                **side_effect_summary(effects),
                "side_effects": [effect.preview() for effect in effects[:MAX_DETAILS]],
            },
        )
    return effects
