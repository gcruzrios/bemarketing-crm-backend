import re
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import COMPLETENESS_WEIGHTS, REQUIRED_FOR_COMPLETE, settings
from app.core.security import utcnow
from app.models import (
    ActorType, Company, Contact, EnrichmentStatus, Lead, LeadEvent, LeadStatus, Opportunity,
    PipelineStage, Service,
)
from app.services.audit import audit, jsonable, snapshot
from app.services.notification_service import notify_managers

FIELD_NAMES = [
    "company_name", "company_type", "website", "city", "contact_first_name", "contact_last_name",
    "contact_job_title", "email", "phone", "whatsapp", "interest_summary", "interested_services",
    "budget_hint", "timeline_hint", "score",
]


# ───────── normalización ─────────
def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    if len(digits) == 8:  # número local de Costa Rica
        return "+506" + digits
    if len(digits) == 11 and digits.startswith("506"):
        return "+" + digits
    return "+" + digits if (value.strip().startswith("+") or len(digits) > 8) else digits


def normalize_email(value: str | None) -> str | None:
    return value.strip().lower() if value and value.strip() else None


def normalize_fields(fields: dict) -> dict:
    out = dict(fields)
    if "email" in out:
        out["email"] = normalize_email(out["email"])
    for k in ("phone", "whatsapp"):
        if k in out:
            out[k] = normalize_phone(out[k])
    for k, v in list(out.items()):
        if isinstance(v, str):
            out[k] = v.strip() or None
    return out


# ───────── completitud ─────────
def _has(lead: Lead, name: str) -> bool:
    if name == "contact_channel":
        return bool(lead.email or lead.whatsapp or lead.phone)
    v = getattr(lead, name)
    return bool(v)


def missing_fields(lead: Lead) -> list[str]:
    out = []
    for name, _ in COMPLETENESS_WEIGHTS:
        if not _has(lead, name):
            out.append("email_or_phone" if name == "contact_channel" else name)
    return out


def recompute(lead: Lead) -> None:
    lead.completeness = min(100, sum(w for n, w in COMPLETENESS_WEIGHTS if _has(lead, n)))
    if lead.enrichment_status == EnrichmentStatus.needs_review:
        return  # solo una persona lo resuelve
    ok = lead.completeness >= settings.COMPLETENESS_THRESHOLD and all(_has(lead, n) for n in REQUIRED_FOR_COMPLETE)
    lead.enrichment_status = EnrichmentStatus.complete if ok else EnrichmentStatus.incomplete


def add_event(session, lead_id, actor_type: ActorType, actor_id, event_type, changes=None, note=None):
    session.add(LeadEvent(lead_id=lead_id, actor_type=actor_type, actor_id=actor_id, event_type=event_type,
                          changes=jsonable(changes) if changes is not None else None, note=note))


# ───────── dedupe ─────────
async def find_duplicate_lead(session: AsyncSession, email, phone, whatsapp, exclude_id=None) -> Lead | None:
    base = [Lead.deleted_at.is_(None), Lead.status != LeadStatus.converted]
    if exclude_id:
        base.append(Lead.id != exclude_id)
    if email:
        r = (await session.execute(select(Lead).where(*base, Lead.email == email).order_by(Lead.created_at).limit(1))).scalar_one_or_none()
        if r:
            return r
    phones = [p for p in (phone, whatsapp) if p]
    if phones:
        return (
            await session.execute(
                select(Lead).where(*base, or_(Lead.phone.in_(phones), Lead.whatsapp.in_(phones))).order_by(Lead.created_at).limit(1)
            )
        ).scalar_one_or_none()
    return None


async def find_existing_contact(session: AsyncSession, email, phone, whatsapp) -> Contact | None:
    conds = []
    if email:
        conds.append(Contact.email == email)
    phones = [p for p in (phone, whatsapp) if p]
    if phones:
        conds += [Contact.phone.in_(phones), Contact.whatsapp.in_(phones)]
    if not conds:
        return None
    return (
        await session.execute(select(Contact).where(Contact.deleted_at.is_(None), or_(*conds)).limit(1))
    ).scalar_one_or_none()


async def valid_service_ids(session: AsyncSession, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not ids:
        return []
    found = (await session.execute(select(Service.id).where(Service.id.in_(ids), Service.deleted_at.is_(None)))).scalars().all()
    return [i for i in ids if i in set(found)]


# ───────── alta manual / edición manual ─────────
async def create_manual(session: AsyncSession, data: dict, user, ip=None) -> Lead:
    data = normalize_fields(data)
    fields = {k: v for k, v in data.items() if k in FIELD_NAMES and v is not None}
    if "interested_services" in fields:
        fields["interested_services"] = await valid_service_ids(session, fields["interested_services"])
    lead = Lead(
        source=data.get("source"), source_reference=data.get("source_reference"),
        first_message=data.get("first_message"), assigned_to=data.get("assigned_to"),
        manual_fields=sorted(fields.keys()), **fields,
    )
    lead.source = lead.source or "other"
    lead.field_meta = {k: {"by": "user", "at": utcnow().isoformat()} for k in fields}
    recompute(lead)
    session.add(lead)
    await session.flush()
    add_event(session, lead.id, ActorType.user, user.id, "created", {"fields": list(fields)})
    await audit(session, "CREATE", "lead", lead.id, user=user, new=snapshot(lead), ip=ip)
    return lead


async def update_manual(session: AsyncSession, lead: Lead, data: dict, user, ip=None) -> Lead:
    data = normalize_fields(data)
    old = snapshot(lead)
    changes = {}
    manual = set(lead.manual_fields or [])
    meta = dict(lead.field_meta or {})
    for k, v in data.items():
        if k == "interested_services" and v is not None:
            v = await valid_service_ids(session, v)
        if k in FIELD_NAMES:
            if getattr(lead, k) != v:
                changes[k] = {"from": getattr(lead, k), "to": v}
            setattr(lead, k, v)
            manual.add(k)
            meta[k] = {"by": "user", "at": utcnow().isoformat()}
        elif k in ("source", "source_reference") and v is not None:
            setattr(lead, k, v)
        elif k == "status" and v is not None:
            if v != lead.status:
                changes["status"] = {"from": lead.status, "to": v}
                add_event(session, lead.id, ActorType.user, user.id, "status_changed", {"from": lead.status, "to": v})
            if v == LeadStatus.converted:
                raise HTTPException(422, "Use POST /leads/{id}/convert para convertir")
            lead.status = v
    lead.manual_fields = sorted(manual)
    lead.field_meta = meta
    recompute(lead)
    if changes:
        add_event(session, lead.id, ActorType.user, user.id, "field_updated", changes)
    await audit(session, "UPDATE", "lead", lead.id, user=user, old=old, new=snapshot(lead), ip=ip)
    return lead


# ───────── upsert del agente ─────────
async def agent_upsert(session: AsyncSession, api_key, payload: dict, ip=None) -> dict:
    fields = normalize_fields({k: v for k, v in payload["fields"].items() if v is not None and k in FIELD_NAMES})
    if "interested_services" in fields:
        fields["interested_services"] = await valid_service_ids(session, fields["interested_services"])
    fields = {k: v for k, v in fields.items() if v not in (None, [], "")}
    conf = payload.get("confidence") or {}
    now = utcnow().isoformat()

    lead = await find_duplicate_lead(session, fields.get("email"), fields.get("phone"), fields.get("whatsapp"))
    created = lead is None
    skipped_manual: list[str] = []
    conflicts: list[dict] = []

    if created:
        lead = Lead(
            source=payload["source"], source_reference=payload.get("source_reference"),
            first_message=payload.get("first_message"), manual_fields=[],
            field_meta={k: {"by": "agent", "confidence": conf.get(k, 0.5), "at": now} for k in fields},
            **fields,
        )
        recompute(lead)
        session.add(lead)
        await session.flush()
        add_event(session, lead.id, ActorType.agent, api_key.id, "created", {"fields": list(fields)}, payload.get("agent_note"))
        await audit(session, "CREATE", "lead", lead.id, api_key=api_key, new=snapshot(lead), ip=ip)
        await notify_managers(session, "lead_new", "Nuevo lead del agente",
                              lead.company_name or lead.contact_first_name or "Sin nombre", "lead", lead.id)
    else:
        old = snapshot(lead)
        manual = set(lead.manual_fields or [])
        meta = dict(lead.field_meta or {})
        changes: dict = {}
        for k, new_val in fields.items():
            cur = getattr(lead, k)
            if k in manual:
                if cur != new_val:
                    skipped_manual.append(k)
                continue
            if cur in (None, [], ""):
                setattr(lead, k, new_val)
                changes[k] = {"from": None, "to": new_val}
                meta[k] = {"by": "agent", "confidence": conf.get(k, 0.5), "at": now}
            elif cur != new_val:
                new_c = conf.get(k, 0.5)
                old_c = (meta.get(k) or {}).get("confidence", 0.0)
                if (meta.get(k) or {}).get("by") != "agent":
                    old_c = 1.0  # valor no proveniente del agente: se trata como confiable
                if new_c > old_c:
                    setattr(lead, k, new_val)
                    changes[k] = {"from": cur, "to": new_val}
                    meta[k] = {"by": "agent", "confidence": new_c, "at": now}
                else:
                    conflicts.append({"field": k, "current": cur, "proposed": new_val,
                                      "current_confidence": old_c, "proposed_confidence": new_c})
        if not lead.first_message and payload.get("first_message"):
            lead.first_message = payload["first_message"]
        lead.field_meta = meta
        was_review = lead.enrichment_status == EnrichmentStatus.needs_review
        if conflicts:
            lead.enrichment_status = EnrichmentStatus.needs_review
        recompute(lead)
        if changes:
            add_event(session, lead.id, ActorType.agent, api_key.id, "enriched", changes, payload.get("agent_note"))
        elif payload.get("agent_note"):
            add_event(session, lead.id, ActorType.agent, api_key.id, "enriched", {}, payload["agent_note"])
        if skipped_manual:
            add_event(session, lead.id, ActorType.agent, api_key.id, "field_updated",
                      {"skipped_manual": skipped_manual}, "Campos editados por una persona; el agente no los modificó")
        if conflicts:
            add_event(session, lead.id, ActorType.agent, api_key.id, "flagged_for_review", {"conflicts": conflicts})
            if not was_review:
                target = lead.assigned_to
                if target:
                    from app.services.notification_service import notify
                    await notify(session, target, "lead_needs_review", "Lead requiere revisión",
                                 lead.company_name, "lead", lead.id)
                else:
                    await notify_managers(session, "lead_needs_review", "Lead requiere revisión",
                                          lead.company_name, "lead", lead.id)
        await audit(session, "UPDATE", "lead", lead.id, api_key=api_key, old=old, new=snapshot(lead), ip=ip)

    existing = await find_existing_contact(session, lead.email, lead.phone, lead.whatsapp)
    return {
        "lead_id": lead.id,
        "created": created,
        "completeness": lead.completeness,
        "enrichment_status": lead.enrichment_status,
        "missing_fields": missing_fields(lead),
        "skipped_manual": skipped_manual,
        "conflicts": conflicts,
        "existing_contact": {"id": existing.id, "company_id": existing.company_id} if existing else None,
    }


# ───────── conversión ─────────
async def similar_company(session: AsyncSession, name: str, threshold=0.6) -> Company | None:
    sim = func.similarity(Company.name, name)
    return (
        await session.execute(
            select(Company).where(Company.deleted_at.is_(None), sim >= threshold).order_by(sim.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def convert_lead(session: AsyncSession, lead: Lead, body: dict, user, ip=None) -> dict:
    """Una sola transacción (la sesión de la request hace commit/rollback al final)."""
    if lead.status == LeadStatus.converted:
        opp = (await session.execute(
            select(Opportunity).where(Opportunity.lead_id == lead.id, Opportunity.deleted_at.is_(None)).limit(1)
        )).scalar_one_or_none()
        raise HTTPException(409, {"message": "El lead ya fue convertido", "opportunity_id": str(opp.id) if opp else None})

    company: Company | None = None
    if body.get("company_id"):
        company = (await session.execute(
            select(Company).where(Company.id == body["company_id"], Company.deleted_at.is_(None)))).scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Empresa no encontrada")
    else:
        if not lead.company_name:
            raise HTTPException(422, "El lead necesita company_name para convertirse")
        if body.get("tax_id"):
            company = (await session.execute(
                select(Company).where(Company.tax_id == body["tax_id"], Company.deleted_at.is_(None)))).scalar_one_or_none()
        if not company and not body.get("force_create_company"):
            dup = await similar_company(session, lead.company_name)
            if dup:
                raise HTTPException(409, {"message": "Existe una empresa muy parecida",
                                          "possible_duplicate": {"id": str(dup.id), "name": dup.name},
                                          "hint": "Reintente con company_id o force_create_company=true"})
        if not company:
            company = Company(name=lead.company_name, legal_name=body.get("legal_name"), tax_id=body.get("tax_id"),
                              company_type=lead.company_type or "other", email=None,
                              website=lead.website, city=lead.city, country="Costa Rica")
            session.add(company)
            await session.flush()
            await audit(session, "CREATE", "company", company.id, user=user, new=snapshot(company), ip=ip)

    contact = None
    if lead.email:
        contact = (await session.execute(select(Contact).where(
            Contact.company_id == company.id, Contact.email == lead.email, Contact.deleted_at.is_(None)).limit(1))).scalar_one_or_none()
    if not contact:
        contact = Contact(first_name=lead.contact_first_name or "Contacto", last_name=lead.contact_last_name,
                          email=lead.email, phone=lead.phone, whatsapp=lead.whatsapp,
                          job_title=lead.contact_job_title, company_id=company.id, lead_id=lead.id)
        session.add(contact)
        await session.flush()
        await audit(session, "CREATE", "contact", contact.id, user=user, new=snapshot(contact), ip=ip)

    opp = None
    if body.get("create_opportunity", True):
        stage = (await session.execute(
            select(PipelineStage).where(PipelineStage.is_won.is_(False), PipelineStage.is_lost.is_(False))
            .order_by(PipelineStage.stage_order).limit(1))).scalar_one_or_none()
        if not stage:
            raise HTTPException(500, "No hay etapas de pipeline configuradas")
        opp = Opportunity(
            name=body.get("opportunity_name") or f"{company.name} – Oportunidad", stage_id=stage.id, lead_id=lead.id,
            company_id=company.id, contact_id=contact.id, expected_value=body.get("expected_value"),
            currency=body.get("currency") or settings.DEFAULT_CURRENCY, probability=stage.probability,
            assigned_to=lead.assigned_to or user.id,
        )
        session.add(opp)
        await session.flush()
        await audit(session, "CREATE", "opportunity", opp.id, user=user, new=snapshot(opp), ip=ip)

    old = snapshot(lead)
    lead.status = LeadStatus.converted
    lead.converted_at = utcnow()
    add_event(session, lead.id, ActorType.user, user.id, "converted",
              {"company_id": company.id, "contact_id": contact.id, "opportunity_id": opp.id if opp else None})
    await audit(session, "CONVERT", "lead", lead.id, user=user, old=old, new=snapshot(lead), ip=ip)
    return {"lead_id": lead.id, "company_id": company.id, "contact_id": contact.id,
            "opportunity_id": opp.id if opp else None}
