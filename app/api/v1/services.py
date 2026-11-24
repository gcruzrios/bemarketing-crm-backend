import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Service, ServiceType, User
from app.schemas.crm import ServiceIn, ServiceOut, ServiceUpdate
from app.services.audit import audit, snapshot

router = APIRouter(prefix="/services", tags=["services"])


async def _get(session, sid) -> Service:
    s = (await session.execute(select(Service).where(Service.id == sid, Service.deleted_at.is_(None)))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Servicio no encontrado")
    return s


async def _check_code(session, code, exclude=None):
    if code:
        q = select(Service.id).where(Service.code == code, Service.deleted_at.is_(None))
        if exclude:
            q = q.where(Service.id != exclude)
        if (await session.execute(q)).first():
            raise HTTPException(409, "Código de servicio ya existe")


@router.get("", summary="Listar catálogo de servicios")
async def list_services(params: PageParams = Depends(), search: str | None = None, service_type: ServiceType | None = None,
                        is_active: bool | None = None, session: AsyncSession = Depends(get_session),
                        _: User = Depends(require_capability("services:read"))):
    stmt = select(Service).where(Service.deleted_at.is_(None)).order_by(Service.name)
    if search:
        stmt = stmt.where(Service.name.ilike(f"%{search}%") | Service.code.ilike(f"%{search}%"))
    if service_type:
        stmt = stmt.where(Service.service_type == service_type)
    if is_active is not None:
        stmt = stmt.where(Service.is_active == is_active)
    return await paginate(session, stmt, params, ServiceOut)


@router.post("", response_model=ServiceOut, status_code=201)
async def create_service(body: ServiceIn, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("services:write"))):
    await _check_code(session, body.code)
    s = Service(**body.model_dump())
    session.add(s)
    await session.flush()
    await audit(session, "CREATE", "service", s.id, user=user, new=snapshot(s), ip=client_ip(request))
    return s


@router.get("/{sid}", response_model=ServiceOut)
async def get_service(sid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("services:read"))):
    return await _get(session, sid)


@router.put("/{sid}", response_model=ServiceOut)
async def update_service(sid: uuid.UUID, body: ServiceUpdate, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("services:write"))):
    s = await _get(session, sid)
    old = snapshot(s)
    data = body.model_dump(exclude_unset=True)
    await _check_code(session, data.get("code"), s.id)
    for k, v in data.items():
        if v is None and k not in ("code", "description"):
            continue
        setattr(s, k, v)
    await audit(session, "UPDATE", "service", s.id, user=user, old=old, new=snapshot(s), ip=client_ip(request))
    return s


@router.delete("/{sid}", status_code=204, summary="Soft delete")
async def delete_service(sid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("services:write"))):
    s = await _get(session, sid)
    s.deleted_at = utcnow()
    await audit(session, "DELETE", "service", s.id, user=user, old=snapshot(s), ip=client_ip(request))
