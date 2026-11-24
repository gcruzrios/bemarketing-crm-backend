import uuid
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import current_user
from app.core.permissions import ROLE_CAPABILITIES
from app.models import Activity, AuditLog, Invoice, Lead, LeadEvent, Opportunity, Quote, User
from app.services.audit import jsonable


class EntityType(str, Enum):
    lead = "lead"
    company = "company"
    opportunity = "opportunity"


router = APIRouter(prefix="/timeline", tags=["timeline"])
CAP = {EntityType.lead: "leads:read", EntityType.company: "companies:read", EntityType.opportunity: "opportunities:read"}


@router.get("", summary="Línea de tiempo de una entidad",
            description="Mezcla lead_events, actividades, cambios de etapa y cotizaciones/facturas relacionadas.")
async def timeline(entity_type: EntityType, entity_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                   user: User = Depends(current_user)):
    caps = ROLE_CAPABILITIES[user.role]
    if CAP[entity_type] not in caps:
        raise HTTPException(403, "Permiso insuficiente")
    ev: list[dict] = []

    def add(at, kind, title, ref=None, detail=None):
        ev.append({"at": at, "kind": kind, "title": title, "ref": ref, "detail": detail})

    opp_filter = {EntityType.lead: Opportunity.lead_id, EntityType.company: Opportunity.company_id,
                  EntityType.opportunity: Opportunity.id}[entity_type]
    opps = (await session.execute(select(Opportunity).where(opp_filter == entity_id, Opportunity.deleted_at.is_(None)))).unique().scalars().all()
    opp_ids = [o.id for o in opps]

    if entity_type == EntityType.lead:
        for e in (await session.execute(select(LeadEvent).where(LeadEvent.lead_id == entity_id))).scalars():
            add(e.created_at, "lead_event", e.event_type, {"actor_type": e.actor_type.value}, jsonable({"changes": e.changes, "note": e.note}))
        act_f = Activity.lead_id == entity_id
    elif entity_type == EntityType.company:
        act_f = Activity.company_id == entity_id
    else:
        act_f = Activity.opportunity_id == entity_id
    for a in (await session.execute(select(Activity).where(act_f, Activity.deleted_at.is_(None)))).scalars():
        add(a.created_at, "activity", f"{a.type.value}: {a.subject}", {"id": str(a.id)}, {"status": a.status.value})

    for o in opps:
        add(o.created_at, "opportunity", f"Oportunidad creada: {o.name}", {"id": str(o.id)})
    if opp_ids:
        for l in (await session.execute(select(AuditLog).where(AuditLog.entity_type == "opportunity", AuditLog.entity_id.in_(opp_ids),
                                                                 AuditLog.action.in_(["MOVE", "WON", "LOST"])))).scalars():
            add(l.created_at, "stage_change", l.action, {"id": str(l.entity_id)}, jsonable(l.new_data))
    q_f = Quote.opportunity_id.in_(opp_ids) if opp_ids else None
    i_f = Invoice.opportunity_id.in_(opp_ids) if opp_ids else None
    if entity_type == EntityType.company:
        q_f, i_f = Quote.company_id == entity_id, Invoice.company_id == entity_id
    if q_f is not None and "quotes:read" in caps:
        for q in (await session.execute(select(Quote).where(q_f, Quote.deleted_at.is_(None)))).scalars():
            add(q.created_at, "quote", f"Cotización {q.quote_number} ({q.status.value})", {"id": str(q.id)}, {"total": str(q.total)})
    if i_f is not None and "invoices:read" in caps:
        for i in (await session.execute(select(Invoice).where(i_f, Invoice.deleted_at.is_(None)))).scalars():
            add(i.created_at, "invoice", f"Factura {i.invoice_number} ({i.status.value})", {"id": str(i.id)}, {"total": str(i.total)})
    ev.sort(key=lambda e: e["at"], reverse=True)
    return ev
