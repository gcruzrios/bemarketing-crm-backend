import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Activity, ActivityStatus, ActivityType, User
from app.schemas.crm import ActivityIn, ActivityOut, ActivityUpdate
from app.services.audit import audit, snapshot

router = APIRouter(prefix="/activities", tags=["activities"])


async def _get(session, aid) -> Activity:
    a = (await session.execute(select(Activity).where(Activity.id == aid, Activity.deleted_at.is_(None)))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Actividad no encontrada")
    return a


@router.get("", summary="Listar actividades")
async def list_activities(params: PageParams = Depends(), status: ActivityStatus | None = None, type: ActivityType | None = None,
                          assigned_to: uuid.UUID | None = None, lead_id: uuid.UUID | None = None, company_id: uuid.UUID | None = None,
                          opportunity_id: uuid.UUID | None = None, contact_id: uuid.UUID | None = None,
                          due_before: datetime | None = None, session: AsyncSession = Depends(get_session),
                          _: User = Depends(require_capability("activities:read"))):
    stmt = select(Activity).where(Activity.deleted_at.is_(None)).order_by(Activity.due_date.asc().nulls_last(), Activity.created_at.desc())
    for col, v in ((Activity.status, status), (Activity.type, type), (Activity.assigned_to, assigned_to), (Activity.lead_id, lead_id),
                   (Activity.company_id, company_id), (Activity.opportunity_id, opportunity_id), (Activity.contact_id, contact_id)):
        if v is not None:
            stmt = stmt.where(col == v)
    if due_before:
        stmt = stmt.where(Activity.due_date <= due_before)
    return await paginate(session, stmt, params, ActivityOut)


@router.post("", response_model=ActivityOut, status_code=201)
async def create(body: ActivityIn, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("activities:write"))):
    a = Activity(**body.model_dump(), status=ActivityStatus.completed if body.type == ActivityType.note else ActivityStatus.pending)
    a.assigned_to = a.assigned_to or user.id
    if a.status == ActivityStatus.completed:
        a.completed_at = utcnow()
    session.add(a)
    await session.flush()
    await audit(session, "CREATE", "activity", a.id, user=user, new=snapshot(a), ip=client_ip(request))
    return a


@router.get("/{aid}", response_model=ActivityOut)
async def get(aid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("activities:read"))):
    return await _get(session, aid)


@router.put("/{aid}", response_model=ActivityOut)
async def update(aid: uuid.UUID, body: ActivityUpdate, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("activities:write"))):
    a = await _get(session, aid)
    old = snapshot(a)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None and k in ("type", "subject", "status"):
            continue
        setattr(a, k, v)
    if a.status == ActivityStatus.completed and not a.completed_at:
        a.completed_at = utcnow()
    await audit(session, "UPDATE", "activity", a.id, user=user, old=old, new=snapshot(a), ip=client_ip(request))
    return a


@router.delete("/{aid}", status_code=204, summary="Soft delete")
async def delete(aid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("activities:write"))):
    a = await _get(session, aid)
    a.deleted_at = utcnow()
    await audit(session, "DELETE", "activity", a.id, user=user, old=snapshot(a), ip=client_ip(request))


@router.post("/{aid}/complete", response_model=ActivityOut, summary="Completar actividad")
async def complete(aid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                   user: User = Depends(require_capability("activities:write"))):
    a = await _get(session, aid)
    if a.status != ActivityStatus.pending:
        raise HTTPException(409, "La actividad no está pendiente")
    a.status, a.completed_at = ActivityStatus.completed, utcnow()
    await audit(session, "COMPLETE", "activity", a.id, user=user, ip=client_ip(request))
    return a
