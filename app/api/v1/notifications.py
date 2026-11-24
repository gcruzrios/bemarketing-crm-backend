import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import current_user
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Notification, User
from app.schemas.crm import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", summary="Mis notificaciones (polling)")
async def list_notifications(params: PageParams = Depends(), unread_only: bool = False,
                             session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    stmt = select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return await paginate(session, stmt, params, NotificationOut)


@router.post("/read-all")
async def read_all(session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    await session.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    return {"detail": "ok"}


@router.post("/{nid}/read", response_model=NotificationOut)
async def read(nid: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    n = (await session.execute(select(Notification).where(Notification.id == nid, Notification.user_id == user.id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notificación no encontrada")
    n.read_at = n.read_at or utcnow()
    return n
