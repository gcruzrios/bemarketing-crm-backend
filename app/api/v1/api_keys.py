import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.permissions import AGENT_SCOPES
from app.core.security import generate_api_key, utcnow
from app.models import ApiKey, User
from app.schemas.auth import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services.audit import audit

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
admin = require_capability("apikeys:manage")


@router.get("", response_model=list[ApiKeyOut], summary="Listar API keys")
async def list_keys(session: AsyncSession = Depends(get_session), _: User = Depends(admin)):
    return (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()


@router.post("", response_model=ApiKeyCreated, status_code=201,
             summary="Crear API key", description="La key en texto plano se devuelve UNA sola vez.")
async def create_key(body: ApiKeyCreate, request: Request, session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    bad = set(body.scopes) - AGENT_SCOPES
    if bad:
        raise HTTPException(422, f"Scopes inválidos: {sorted(bad)}")
    key, prefix, h = generate_api_key()
    k = ApiKey(name=body.name, key_prefix=prefix, key_hash=h, scopes=body.scopes, created_by=me.id)
    session.add(k)
    await session.flush()
    await audit(session, "CREATE", "api_key", k.id, user=me, new={"name": k.name, "scopes": k.scopes, "prefix": prefix}, ip=client_ip(request))
    return ApiKeyCreated(**ApiKeyOut.model_validate(k).model_dump(), key=key)


@router.post("/{key_id}/revoke", response_model=ApiKeyOut, summary="Revocar API key")
async def revoke(key_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    k = (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one_or_none()
    if not k:
        raise HTTPException(404, "API key no encontrada")
    k.is_active = False
    k.revoked_at = k.revoked_at or utcnow()
    await audit(session, "REVOKE", "api_key", k.id, user=me, ip=client_ip(request))
    return k
