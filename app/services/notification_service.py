import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, User, UserRole


async def notify(session: AsyncSession, user_id: uuid.UUID, type_: str, title: str, body: str | None = None,
                 entity_type: str | None = None, entity_id: uuid.UUID | None = None):
    session.add(Notification(user_id=user_id, type=type_, title=title, body=body,
                             entity_type=entity_type, entity_id=entity_id))


async def notify_managers(session: AsyncSession, type_: str, title: str, body: str | None = None,
                          entity_type: str | None = None, entity_id: uuid.UUID | None = None):
    ids = (
        await session.execute(
            select(User.id).where(User.role.in_([UserRole.admin, UserRole.manager]),
                                  User.is_active.is_(True), User.deleted_at.is_(None))
        )
    ).scalars().all()
    for uid in ids:
        await notify(session, uid, type_, title, body, entity_type, entity_id)
