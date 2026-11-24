import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import (
    ActorType, CompanyType, EnrichmentStatus, Lead, LeadEvent, LeadSource, LeadStatus, User,
)
from app.schemas.leads import (
    AssignIn, ConvertIn, LeadCreate, LeadEventOut, LeadOut, LeadUpdate, ResolveReviewIn,
)
from app.services import lead_service as ls
from app.services.audit import audit, snapshot
from app.services.notification_service import notify

router = APIRouter(prefix="/leads", tags=["leads"])


async def get_lead(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.deleted_at.is_(None)))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    return lead


@router.get("", summary="Listar leads", description="Filtros: status, source, company_type, enrichment_status, assigned_to, fechas, search.")
async def list_leads(
    params: PageParams = Depends(), status: LeadStatus | None = None, source: LeadSource | None = None,
    company_type: CompanyType | None = None, enrichment_status: EnrichmentStatus | None = None,
    assigned_to: uuid.UUID | None = None, created_from: date | None = None, created_to: date | None = None,
    search: str | None = None, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("leads:read")),
):
    stmt = select(Lead).where(Lead.deleted_at.is_(None)).order_by(Lead.created_at.desc())
    for col, val in ((Lead.status, status), (Lead.source, source), (Lead.company_type, company_type),
                     (Lead.enrichment_status, enrichment_status), (Lead.assigned_to, assigned_to)):
        if val is not None:
            stmt = stmt.where(col == val)
    if created_from:
        stmt = stmt.where(Lead.created_at >= created_from)
    if created_to:
        stmt = stmt.where(Lead.created_at < date.fromordinal(created_to.toordinal() + 1))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Lead.company_name.ilike(like), Lead.contact_first_name.ilike(like),
                              Lead.contact_last_name.ilike(like), Lead.email.ilike(like)))
    return await paginate(session, stmt, params, LeadOut)


@router.post("", response_model=LeadOut, status_code=201, summary="Alta manual de lead")
async def create_lead(body: LeadCreate, request: Request, session: AsyncSession = Depends(get_session),
                      user: User = Depends(require_capability("leads:write"))):
    return await ls.create_manual(session, body.model_dump(exclude_unset=True), user, client_ip(request))


@router.get("/{lead_id}", response_model=LeadOut)
async def read_lead(lead_id: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("leads:read"))):
    return await get_lead(session, lead_id)


@router.put("/{lead_id}", response_model=LeadOut, summary="Actualización parcial",
            description="Solo los campos enviados cambian y se agregan a manual_fields (el agente no los pisa).")
async def update_lead(lead_id: uuid.UUID, body: LeadUpdate, request: Request, session: AsyncSession = Depends(get_session),
                      user: User = Depends(require_capability("leads:write"))):
    lead = await get_lead(session, lead_id)
    return await ls.update_manual(session, lead, body.model_dump(exclude_unset=True), user, client_ip(request))


@router.delete("/{lead_id}", status_code=204, summary="Soft delete")
async def delete_lead(lead_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                      user: User = Depends(require_capability("leads:write"))):
    lead = await get_lead(session, lead_id)
    lead.deleted_at = utcnow()
    await audit(session, "DELETE", "lead", lead.id, user=user, old=snapshot(lead), ip=client_ip(request))


@router.post("/{lead_id}/assign", response_model=LeadOut, summary="Asignar vendedor")
async def assign(lead_id: uuid.UUID, body: AssignIn, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("leads:assign"))):
    lead = await get_lead(session, lead_id)
    if body.assigned_to:
        target = (await session.execute(select(User).where(User.id == body.assigned_to, User.is_active.is_(True),
                                                           User.deleted_at.is_(None)))).scalar_one_or_none()
        if not target:
            raise HTTPException(422, "Usuario destino no existe o está inactivo")
    old = lead.assigned_to
    lead.assigned_to = body.assigned_to
    ls.add_event(session, lead.id, ActorType.user, user.id, "assigned", {"from": old, "to": body.assigned_to})
    await audit(session, "ASSIGN", "lead", lead.id, user=user, old={"assigned_to": str(old) if old else None},
                new={"assigned_to": str(body.assigned_to) if body.assigned_to else None}, ip=client_ip(request))
    if body.assigned_to and body.assigned_to != user.id:
        await notify(session, body.assigned_to, "lead_assigned", "Se te asignó un lead",
                     lead.company_name, "lead", lead.id)
    return lead


@router.post("/{lead_id}/convert", summary="Convertir lead (transaccional)",
             description="Crea/usa Company + Contact + Opportunity en una sola transacción. 409 si ya está convertido "
                         "o si existe una empresa muy parecida (reintentar con company_id o force_create_company).")
async def convert(lead_id: uuid.UUID, body: ConvertIn, request: Request, session: AsyncSession = Depends(get_session),
                  user: User = Depends(require_capability("leads:convert"))):
    lead = await get_lead(session, lead_id)
    res = await ls.convert_lead(session, lead, body.model_dump(), user, client_ip(request))
    return res


@router.post("/{lead_id}/resolve-review", response_model=LeadOut, summary="Resolver needs_review")
async def resolve_review(lead_id: uuid.UUID, body: ResolveReviewIn, request: Request,
                         session: AsyncSession = Depends(get_session), user: User = Depends(require_capability("leads:write"))):
    lead = await get_lead(session, lead_id)
    if lead.enrichment_status != EnrichmentStatus.needs_review:
        raise HTTPException(409, "El lead no está en needs_review")
    if body.accept_fields:
        bad = set(body.accept_fields) - set(ls.FIELD_NAMES)
        if bad:
            raise HTTPException(422, f"Campos inválidos: {sorted(bad)}")
        await ls.update_manual(session, lead, body.accept_fields, user, client_ip(request))
    lead.enrichment_status = EnrichmentStatus.incomplete
    ls.recompute(lead)
    ls.add_event(session, lead.id, ActorType.user, user.id, "field_updated", {"resolved_review": True}, body.note)
    await audit(session, "RESOLVE_REVIEW", "lead", lead.id, user=user, ip=client_ip(request))
    return lead


@router.get("/{lead_id}/events", response_model=list[LeadEventOut], summary="Bitácora del lead")
async def events(lead_id: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("leads:read"))):
    await get_lead(session, lead_id)
    return (await session.execute(select(LeadEvent).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.created_at.desc()))).scalars().all()
