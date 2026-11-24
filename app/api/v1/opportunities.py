import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import (
    Company, Contact, Opportunity, OpportunityService, OpportunityStatus, PipelineStage, Service, User,
)
from app.schemas.crm import (
    LostIn, MoveIn, OpportunityIn, OpportunityOut, OpportunityServiceOut, OpportunityUpdate,
)
from app.services.audit import audit, snapshot
from app.services.doc_service import q2

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


async def _get(session, oid) -> Opportunity:
    o = (await session.execute(select(Opportunity).where(Opportunity.id == oid, Opportunity.deleted_at.is_(None)))).unique().scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Oportunidad no encontrada")
    return o


async def _validate(session, company_id, contact_id):
    if not (await session.execute(select(Company.id).where(Company.id == company_id, Company.deleted_at.is_(None)))).first():
        raise HTTPException(422, "company_id no existe")
    if contact_id and not (await session.execute(select(Contact.id).where(
            Contact.id == contact_id, Contact.company_id == company_id, Contact.deleted_at.is_(None)))).first():
        raise HTTPException(422, "El contacto no pertenece a la empresa")


async def _set_services(session, opp, items):
    await session.execute(delete(OpportunityService).where(OpportunityService.opportunity_id == opp.id))
    total = Decimal(0)
    for it in items:
        svc = (await session.execute(select(Service).where(Service.id == it.service_id, Service.deleted_at.is_(None)))).scalar_one_or_none()
        if not svc:
            raise HTTPException(422, f"Servicio {it.service_id} no existe")
        unit = it.unit_price if it.unit_price is not None else svc.price
        line = q2(it.quantity * unit - it.discount)
        session.add(OpportunityService(opportunity_id=opp.id, service_id=svc.id, quantity=it.quantity,
                                       unit_price=unit, discount=it.discount, total=line))
        total += line
    return total


async def _apply_stage(session, opp: Opportunity, stage: PipelineStage):
    opp.stage_id = stage.id
    opp.stage = stage
    if stage.is_won:
        opp.status, opp.closed_at, opp.probability = OpportunityStatus.won, utcnow(), 100
    elif stage.is_lost:
        opp.status, opp.closed_at, opp.probability = OpportunityStatus.lost, utcnow(), 0
    else:
        opp.status, opp.closed_at, opp.lost_reason, opp.probability = OpportunityStatus.open, None, None, stage.probability


@router.get("", summary="Listar oportunidades")
async def list_opps(params: PageParams = Depends(), status: OpportunityStatus | None = None, stage_id: uuid.UUID | None = None,
                    company_id: uuid.UUID | None = None, assigned_to: uuid.UUID | None = None, search: str | None = None,
                    session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("opportunities:read"))):
    stmt = select(Opportunity).where(Opportunity.deleted_at.is_(None)).order_by(Opportunity.created_at.desc())
    for col, v in ((Opportunity.status, status), (Opportunity.stage_id, stage_id), (Opportunity.company_id, company_id), (Opportunity.assigned_to, assigned_to)):
        if v is not None:
            stmt = stmt.where(col == v)
    if search:
        stmt = stmt.where(Opportunity.name.ilike(f"%{search}%"))
    return await paginate(session, stmt, params, OpportunityOut, unique=True)


@router.post("", response_model=OpportunityOut, status_code=201)
async def create_opp(body: OpportunityIn, request: Request, session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_capability("opportunities:write"))):
    await _validate(session, body.company_id, body.contact_id)
    if body.stage_id:
        stage = (await session.execute(select(PipelineStage).where(PipelineStage.id == body.stage_id))).scalar_one_or_none()
        if not stage:
            raise HTTPException(422, "stage_id no existe")
    else:
        stage = (await session.execute(select(PipelineStage).where(PipelineStage.is_won.is_(False), PipelineStage.is_lost.is_(False))
                                       .order_by(PipelineStage.stage_order).limit(1))).scalar_one()
    data = body.model_dump(exclude={"services", "stage_id"})
    opp = Opportunity(**data, stage_id=stage.id, assigned_to=body.assigned_to or user.id)
    session.add(opp)
    await session.flush()
    await _apply_stage(session, opp, stage)
    if body.probability is not None and opp.status == OpportunityStatus.open:
        opp.probability = body.probability
    if body.services:
        total = await _set_services(session, opp, body.services)
        if opp.expected_value is None:
            opp.expected_value = total
    await audit(session, "CREATE", "opportunity", opp.id, user=user, new=snapshot(opp), ip=client_ip(request))
    return opp


@router.get("/{oid}", response_model=OpportunityOut)
async def get_opp(oid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("opportunities:read"))):
    return await _get(session, oid)


@router.get("/{oid}/services", response_model=list[OpportunityServiceOut])
async def opp_services(oid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("opportunities:read"))):
    await _get(session, oid)
    return (await session.execute(select(OpportunityService).where(OpportunityService.opportunity_id == oid))).scalars().all()


@router.put("/{oid}", response_model=OpportunityOut)
async def update_opp(oid: uuid.UUID, body: OpportunityUpdate, request: Request, session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_capability("opportunities:write"))):
    opp = await _get(session, oid)
    old = snapshot(opp)
    data = body.model_dump(exclude_unset=True, exclude={"services"})
    if "contact_id" in data:
        await _validate(session, opp.company_id, data["contact_id"])
    for k, v in data.items():
        if v is None and k in ("name", "currency"):
            continue
        setattr(opp, k, v)
    if body.services is not None:
        total = await _set_services(session, opp, body.services)
        if "expected_value" not in data:
            opp.expected_value = total
    await audit(session, "UPDATE", "opportunity", opp.id, user=user, old=old, new=snapshot(opp), ip=client_ip(request))
    return opp


@router.delete("/{oid}", status_code=204, summary="Soft delete")
async def delete_opp(oid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_capability("opportunities:write"))):
    opp = await _get(session, oid)
    opp.deleted_at = utcnow()
    await audit(session, "DELETE", "opportunity", opp.id, user=user, old=snapshot(opp), ip=client_ip(request))


@router.post("/{oid}/move", response_model=OpportunityOut, summary="Mover de etapa")
async def move(oid: uuid.UUID, body: MoveIn, request: Request, session: AsyncSession = Depends(get_session),
               user: User = Depends(require_capability("pipeline:move"))):
    opp = await _get(session, oid)
    stage = (await session.execute(select(PipelineStage).where(PipelineStage.id == body.stage_id))).scalar_one_or_none()
    if not stage:
        raise HTTPException(422, "stage_id no existe")
    old = {"stage_id": str(opp.stage_id), "status": opp.status.value}
    await _apply_stage(session, opp, stage)
    await audit(session, "MOVE", "opportunity", opp.id, user=user, old=old, new={"stage_id": str(stage.id), "status": opp.status.value}, ip=client_ip(request))
    return opp


@router.post("/{oid}/won", response_model=OpportunityOut, summary="Marcar como ganada")
async def won(oid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
              user: User = Depends(require_capability("pipeline:move"))):
    opp = await _get(session, oid)
    stage = (await session.execute(select(PipelineStage).where(PipelineStage.is_won))).scalar_one()
    await _apply_stage(session, opp, stage)
    await audit(session, "WON", "opportunity", opp.id, user=user, ip=client_ip(request))
    return opp


@router.post("/{oid}/lost", response_model=OpportunityOut, summary="Marcar como perdida")
async def lost(oid: uuid.UUID, body: LostIn, request: Request, session: AsyncSession = Depends(get_session),
               user: User = Depends(require_capability("pipeline:move"))):
    opp = await _get(session, oid)
    stage = (await session.execute(select(PipelineStage).where(PipelineStage.is_lost))).scalar_one()
    await _apply_stage(session, opp, stage)
    opp.lost_reason = body.lost_reason
    await audit(session, "LOST", "opportunity", opp.id, user=user, new={"lost_reason": body.lost_reason}, ip=client_ip(request))
    return opp
