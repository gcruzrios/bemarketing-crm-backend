import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import AgentPrincipal, client_ip, require_scope
from app.core.limiter import limiter
from app.models import Activity, ActivityType, IngestEvent, IngestStatus, Lead
from app.schemas.leads import AgentActivityIn, AgentUpsertIn
from app.services import lead_service as ls
from app.services.audit import audit, jsonable
from app.services.notification_service import notify, notify_managers

router = APIRouter(prefix="/agent", tags=["agent"])

UPSERT_EXAMPLE = {
    "source": "whatsapp", "source_reference": "conv_8f21",
    "first_message": "Hola, tenemos un hotel en Quepos...",
    "fields": {"company_name": "Hotel Ejemplo", "company_type": "hotel", "whatsapp": "+506 8888-8888", "city": "Quepos"},
    "confidence": {"company_type": 0.95}, "agent_note": "Depende de Booking.",
}


@router.post("/leads/upsert", summary="Crear o completar un lead (idempotente)",
             description="Scope `leads:ingest`. Header `Idempotency-Key` recomendado. Respeta `manual_fields`, "
                         "resuelve conflictos por confianza y devuelve `missing_fields` para guiar al agente.")
async def upsert(body: AgentUpsertIn, request: Request, idempotency_key: str | None = Header(None, max_length=255),
                 session: AsyncSession = Depends(get_session), p: AgentPrincipal = Depends(require_scope("leads:ingest"))):
    key = p.api_key
    if idempotency_key:
        prev = (await session.execute(select(IngestEvent).where(
            IngestEvent.api_key_id == key.id, IngestEvent.idempotency_key == idempotency_key))).scalar_one_or_none()
        if prev and prev.response is not None:
            return prev.response
    payload = body.model_dump(mode="json", exclude_unset=False)
    payload["fields"] = {k: v for k, v in payload["fields"].items() if v is not None}
    result = jsonable(await ls.agent_upsert(session, key, payload, client_ip(request)))
    if idempotency_key:
        session.add(IngestEvent(api_key_id=key.id, idempotency_key=idempotency_key, payload=payload,
                                response=result, result_lead_id=uuid.UUID(result["lead_id"]), status=IngestStatus.processed))
    return result


@router.get("/leads/lookup", summary="¿Ya existe un lead con este contacto?")
async def lookup(email: str | None = None, phone: str | None = None, whatsapp: str | None = None,
                 session: AsyncSession = Depends(get_session), _: AgentPrincipal = Depends(require_scope("leads:read"))):
    if not (email or phone or whatsapp):
        raise HTTPException(422, "Indique email, phone o whatsapp")
    lead = await ls.find_duplicate_lead(session, ls.normalize_email(email), ls.normalize_phone(phone), ls.normalize_phone(whatsapp))
    if not lead:
        return {"found": False}
    return {"found": True, "lead_id": lead.id, "status": lead.status, "completeness": lead.completeness,
            "enrichment_status": lead.enrichment_status, "missing_fields": ls.missing_fields(lead)}


@router.get("/leads/{lead_id}", summary="Estado de la ficha (sin datos internos)")
async def get_lead(lead_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                   _: AgentPrincipal = Depends(require_scope("leads:read"))):
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.deleted_at.is_(None)))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    ficha = {f: getattr(lead, f) for f in ls.FIELD_NAMES if f != "score"}
    return {"id": lead.id, "status": lead.status, "source": lead.source, "completeness": lead.completeness,
            "enrichment_status": lead.enrichment_status, "missing_fields": ls.missing_fields(lead), "fields": ficha}


@router.post("/leads/{lead_id}/activities", status_code=201, summary="Crear tarea de seguimiento 'pendiente humano'")
async def create_activity(lead_id: uuid.UUID, body: AgentActivityIn, request: Request,
                          session: AsyncSession = Depends(get_session), p: AgentPrincipal = Depends(require_scope("activities:create"))):
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.deleted_at.is_(None)))).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    try:
        atype = ActivityType(body.type)
    except ValueError:
        raise HTTPException(422, "type inválido")
    if atype not in (ActivityType.follow_up, ActivityType.task, ActivityType.note):
        raise HTTPException(422, "El agente solo puede crear follow_up, task o note")
    act = Activity(type=atype, subject=body.subject, description=body.description, lead_id=lead.id,
                   assigned_to=lead.assigned_to, due_date=body.due_date, created_by_agent=True)
    session.add(act)
    await session.flush()
    await audit(session, "CREATE", "activity", act.id, api_key=p.api_key, new={"subject": act.subject, "lead_id": str(lead.id)}, ip=client_ip(request))
    if lead.assigned_to:
        await notify(session, lead.assigned_to, "activity_created", "Tarea creada por el agente", act.subject, "lead", lead.id)
    else:
        await notify_managers(session, "activity_created", "Tarea creada por el agente", act.subject, "lead", lead.id)
    return {"activity_id": act.id}
