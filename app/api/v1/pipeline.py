import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.models import Opportunity, OpportunityStatus, PipelineStage, User
from app.schemas.crm import OpportunityOut, ReorderItem, StageIn, StageOut, StageUpdate
from app.services.audit import audit, snapshot

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
cfg = require_capability("pipeline:configure")


async def _stage(session, sid) -> PipelineStage:
    s = (await session.execute(select(PipelineStage).where(PipelineStage.id == sid))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Etapa no encontrada")
    return s


@router.get("/stages", response_model=list[StageOut], summary="Etapas del pipeline")
async def stages(session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("opportunities:read"))):
    return (await session.execute(select(PipelineStage).order_by(PipelineStage.stage_order))).scalars().all()


@router.post("/stages", response_model=StageOut, status_code=201)
async def create_stage(body: StageIn, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(cfg)):
    if body.is_won or body.is_lost:
        raise HTTPException(409, "Ya existe una etapa ganada y una perdida; edítelas en lugar de crear otras")
    order = body.stage_order
    if order is None:
        # antes de Ganado/Perdido
        first_terminal = (await session.execute(select(func.min(PipelineStage.stage_order)).where(
            PipelineStage.is_won | PipelineStage.is_lost))).scalar_one()
        order = first_terminal if first_terminal is not None else 1
        await session.execute(PipelineStage.__table__.update().where(PipelineStage.stage_order >= order).values(stage_order=PipelineStage.stage_order + 1))
    s = PipelineStage(name=body.name, stage_order=order, probability=body.probability, color=body.color)
    session.add(s)
    await session.flush()
    await audit(session, "CREATE", "pipeline_stage", s.id, user=user, new=snapshot(s), ip=client_ip(request))
    return s


@router.put("/stages/{sid}", response_model=StageOut)
async def update_stage(sid: uuid.UUID, body: StageUpdate, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(cfg)):
    s = await _stage(session, sid)
    old = snapshot(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "color":
            setattr(s, k, v)
    await audit(session, "UPDATE", "pipeline_stage", s.id, user=user, old=old, new=snapshot(s), ip=client_ip(request))
    return s


@router.post("/stages/reorder", response_model=list[StageOut])
async def reorder(items: list[ReorderItem], request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(cfg)):
    stages_ = {s.id: s for s in (await session.execute(select(PipelineStage))).scalars().all()}
    if {i.id for i in items} != set(stages_):
        raise HTTPException(422, "Debe incluir todas las etapas")
    for i in items:
        stages_[i.id].stage_order = i.stage_order
    await audit(session, "REORDER", "pipeline_stage", None, user=user, new={"order": [{"id": str(i.id), "o": i.stage_order} for i in items]}, ip=client_ip(request))
    return sorted(stages_.values(), key=lambda s: s.stage_order)


@router.delete("/stages/{sid}", status_code=204, summary="Eliminar etapa", description="409 si tiene oportunidades abiertas.")
async def delete_stage(sid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(cfg)):
    s = await _stage(session, sid)
    if s.is_won or s.is_lost:
        raise HTTPException(409, "No se puede eliminar la etapa Ganado/Perdido")
    n = (await session.execute(select(func.count()).select_from(Opportunity).where(
        Opportunity.stage_id == s.id, Opportunity.deleted_at.is_(None)))).scalar_one()
    if n:
        raise HTTPException(409, f"La etapa tiene {n} oportunidades")
    await audit(session, "DELETE", "pipeline_stage", s.id, user=user, old=snapshot(s), ip=client_ip(request))
    await session.delete(s)


@router.get("/board", summary="Kanban: etapas con oportunidades abiertas")
async def board(session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("opportunities:read"))):
    st = (await session.execute(select(PipelineStage).order_by(PipelineStage.stage_order))).scalars().all()
    opps = (await session.execute(select(Opportunity).where(
        Opportunity.deleted_at.is_(None), Opportunity.status == OpportunityStatus.open).order_by(Opportunity.updated_at.desc()))).unique().scalars().all()
    by = defaultdict(list)
    for o in opps:
        by[o.stage_id].append(OpportunityOut.model_validate(o))
    return [{"stage": StageOut.model_validate(s), "opportunities": by[s.id],
             "total_value": sum((o.expected_value or 0) for o in by[s.id])} for s in st if not (s.is_won or s.is_lost)]
