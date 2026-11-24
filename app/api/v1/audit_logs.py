import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import require_capability
from app.core.pagination import PageParams, paginate
from app.models import AuditLog, User
from app.schemas.crm import AuditOut

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", summary="Auditoría (admin/manager)")
async def list_audit(params: PageParams = Depends(), entity_type: str | None = None, entity_id: uuid.UUID | None = None,
                     action: str | None = None, actor_type: str | None = None, session: AsyncSession = Depends(get_session),
                     _: User = Depends(require_capability("audit:read"))):
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    return await paginate(session, stmt, params, AuditOut)
