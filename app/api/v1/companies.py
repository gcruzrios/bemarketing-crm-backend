import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Company, Contact, Opportunity, OpportunityStatus, User
from app.schemas.crm import CompanyIn, CompanyOut, CompanyUpdate
from app.services.audit import audit, snapshot
from app.services.lead_service import similar_company

router = APIRouter(prefix="/companies", tags=["companies"])


async def _get(session, cid) -> Company:
    c = (await session.execute(select(Company).where(Company.id == cid, Company.deleted_at.is_(None)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Empresa no encontrada")
    return c


@router.get("", summary="Listar empresas")
async def list_companies(params: PageParams = Depends(), search: str | None = None,
                         session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("companies:read"))):
    stmt = select(Company).where(Company.deleted_at.is_(None)).order_by(Company.name)
    if search:
        stmt = stmt.where(Company.name.ilike(f"%{search}%") | Company.legal_name.ilike(f"%{search}%") | Company.tax_id.ilike(f"%{search}%"))
    return await paginate(session, stmt, params, CompanyOut)


@router.post("", response_model=CompanyOut, status_code=201,
             summary="Crear empresa", description="Devuelve `possible_duplicate` (por tax_id o nombre similar) sin bloquear.")
async def create_company(body: CompanyIn, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("companies:write"))):
    dup = None
    if body.tax_id:
        d = (await session.execute(select(Company).where(Company.tax_id == body.tax_id, Company.deleted_at.is_(None)))).scalar_one_or_none()
        if d:
            raise HTTPException(409, {"message": "tax_id ya registrado", "possible_duplicate": {"id": str(d.id), "name": d.name}})
    else:
        d = await similar_company(session, body.name)
        dup = {"id": d.id, "name": d.name} if d else None
    c = Company(**body.model_dump())
    session.add(c)
    await session.flush()
    await audit(session, "CREATE", "company", c.id, user=user, new=snapshot(c), ip=client_ip(request))
    out = CompanyOut.model_validate(c)
    out.possible_duplicate = dup
    return out


@router.get("/{cid}", response_model=CompanyOut)
async def get_company(cid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("companies:read"))):
    return await _get(session, cid)


@router.put("/{cid}", response_model=CompanyOut)
async def update_company(cid: uuid.UUID, body: CompanyUpdate, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("companies:write"))):
    c = await _get(session, cid)
    old = snapshot(c)
    data = body.model_dump(exclude_unset=True)
    if data.get("tax_id") and (await session.execute(select(Company.id).where(
            Company.tax_id == data["tax_id"], Company.id != c.id, Company.deleted_at.is_(None)))).first():
        raise HTTPException(409, "tax_id ya registrado")
    for k, v in data.items():
        if k == "name" and v is None:
            continue
        setattr(c, k, v)
    await audit(session, "UPDATE", "company", c.id, user=user, old=old, new=snapshot(c), ip=client_ip(request))
    return c


@router.delete("/{cid}", status_code=204, summary="Soft delete")
async def delete_company(cid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("companies:write"))):
    c = await _get(session, cid)
    open_opps = (await session.execute(select(func.count()).select_from(Opportunity).where(
        Opportunity.company_id == c.id, Opportunity.deleted_at.is_(None), Opportunity.status == OpportunityStatus.open))).scalar_one()
    if open_opps:
        raise HTTPException(409, "La empresa tiene oportunidades abiertas")
    c.deleted_at = utcnow()
    await audit(session, "DELETE", "company", c.id, user=user, old=snapshot(c), ip=client_ip(request))
