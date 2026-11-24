import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Company, Contact, User
from app.schemas.crm import ContactIn, ContactOut, ContactUpdate
from app.services.audit import audit, snapshot
from app.services.lead_service import normalize_email, normalize_phone

router = APIRouter(prefix="/contacts", tags=["contacts"])


async def _get(session, cid) -> Contact:
    c = (await session.execute(select(Contact).where(Contact.id == cid, Contact.deleted_at.is_(None)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contacto no encontrado")
    return c


async def _check_company(session, company_id):
    if not (await session.execute(select(Company.id).where(Company.id == company_id, Company.deleted_at.is_(None)))).first():
        raise HTTPException(422, "company_id no existe")


@router.get("", summary="Listar contactos")
async def list_contacts(params: PageParams = Depends(), search: str | None = None, company_id: uuid.UUID | None = None,
                        session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("contacts:read"))):
    stmt = select(Contact).where(Contact.deleted_at.is_(None)).order_by(Contact.first_name)
    if company_id:
        stmt = stmt.where(Contact.company_id == company_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like)))
    return await paginate(session, stmt, params, ContactOut)


@router.post("", response_model=ContactOut, status_code=201, summary="Crear contacto",
             description="Devuelve `possible_duplicate` por email sin bloquear.")
async def create_contact(body: ContactIn, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("contacts:write"))):
    await _check_company(session, body.company_id)
    data = body.model_dump()
    data["email"] = normalize_email(data["email"])
    data["phone"], data["whatsapp"] = normalize_phone(data["phone"]), normalize_phone(data["whatsapp"])
    dup = None
    if data["email"]:
        d = (await session.execute(select(Contact).where(Contact.email == data["email"], Contact.deleted_at.is_(None)).limit(1))).scalar_one_or_none()
        dup = {"id": d.id, "name": f"{d.first_name} {d.last_name or ''}".strip()} if d else None
    c = Contact(**data)
    session.add(c)
    await session.flush()
    await audit(session, "CREATE", "contact", c.id, user=user, new=snapshot(c), ip=client_ip(request))
    out = ContactOut.model_validate(c)
    out.possible_duplicate = dup
    return out


@router.get("/{cid}", response_model=ContactOut)
async def get_contact(cid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("contacts:read"))):
    return await _get(session, cid)


@router.put("/{cid}", response_model=ContactOut)
async def update_contact(cid: uuid.UUID, body: ContactUpdate, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("contacts:write"))):
    c = await _get(session, cid)
    old = snapshot(c)
    data = body.model_dump(exclude_unset=True)
    if data.get("company_id"):
        await _check_company(session, data["company_id"])
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    for k in ("phone", "whatsapp"):
        if k in data:
            data[k] = normalize_phone(data[k])
    for k, v in data.items():
        if k in ("first_name", "company_id") and v is None:
            continue
        setattr(c, k, v)
    await audit(session, "UPDATE", "contact", c.id, user=user, old=old, new=snapshot(c), ip=client_ip(request))
    return c


@router.delete("/{cid}", status_code=204, summary="Soft delete")
async def delete_contact(cid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("contacts:write"))):
    c = await _get(session, cid)
    c.deleted_at = utcnow()
    await audit(session, "DELETE", "contact", c.id, user=user, old=snapshot(c), ip=client_ip(request))
