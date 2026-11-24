import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActorType, AuditLog


def jsonable(obj):
    return json.loads(json.dumps(obj, default=_default))


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (uuid.UUID, Decimal)):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    return str(o)


def snapshot(obj, fields: list[str] | None = None) -> dict:
    cols = fields or [c.key for c in obj.__table__.columns if c.key not in ("pdf_data", "password_hash")]
    loaded = inspect(obj).dict  # nunca dispara lazy-load (async)
    return jsonable({c: loaded.get(c) for c in cols if c in loaded})


async def audit(
    session: AsyncSession,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    *,
    user=None,
    api_key=None,
    old=None,
    new=None,
    ip: str | None = None,
):
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            actor_type=ActorType.agent if api_key else (ActorType.user if user else ActorType.system),
            api_key_id=api_key.id if api_key else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_data=old,
            new_data=new,
            ip_address=ip,
        )
    )
