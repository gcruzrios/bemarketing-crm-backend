import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import hash_password, utcnow
from app.models import RefreshToken, User, UserRole
from app.schemas.auth import UserCreate, UserOut, UserUpdate
from app.services.audit import audit, snapshot

router = APIRouter(prefix="/users", tags=["users"])
admin = require_capability("users:manage")


async def _get(session, uid) -> User:
    u = (await session.execute(select(User).where(User.id == uid, User.deleted_at.is_(None)))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    return u


@router.get("", summary="Listar usuarios")
async def list_users(params: PageParams = Depends(), search: str | None = None, role: UserRole | None = None,
                     session: AsyncSession = Depends(get_session), _: User = Depends(admin)):
    stmt = select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)
    if role:
        stmt = stmt.where(User.role == role)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(User.email.ilike(like) | User.first_name.ilike(like) | User.last_name.ilike(like))
    return await paginate(session, stmt, params, UserOut)


@router.post("", response_model=UserOut, status_code=201, summary="Crear usuario")
async def create_user(body: UserCreate, request: Request, session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    if (await session.execute(select(User.id).where(func.lower(User.email) == body.email.lower(), User.deleted_at.is_(None)))).first():
        raise HTTPException(409, "Ya existe un usuario con ese email")
    u = User(first_name=body.first_name, last_name=body.last_name, email=body.email.lower(),
             password_hash=hash_password(body.password), role=body.role)
    session.add(u)
    await session.flush()
    await audit(session, "CREATE", "user", u.id, user=me, new=snapshot(u), ip=client_ip(request))
    return u


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(admin)):
    return await _get(session, user_id)


@router.put("/{user_id}", response_model=UserOut, summary="Actualizar usuario")
async def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request,
                      session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    u = await _get(session, user_id)
    old = snapshot(u)
    data = body.model_dump(exclude_unset=True)
    if "email" in data and data["email"]:
        data["email"] = data["email"].lower()
        if (await session.execute(select(User.id).where(func.lower(User.email) == data["email"], User.id != u.id, User.deleted_at.is_(None)))).first():
            raise HTTPException(409, "Email en uso")
    if u.id == me.id and (data.get("role", u.role) != UserRole.admin or data.get("is_active") is False):
        raise HTTPException(409, "No puede quitarse su propio rol admin ni desactivarse")
    for k, v in data.items():
        setattr(u, k, v)
    if data.get("is_active") is False:
        await session.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await audit(session, "UPDATE", "user", u.id, user=me, old=old, new=snapshot(u), ip=client_ip(request))
    return u


@router.post("/{user_id}/deactivate", summary="Desactivar (soft delete) y revocar sesiones")
async def deactivate(user_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    u = await _get(session, user_id)
    if u.id == me.id:
        raise HTTPException(409, "No puede desactivarse a sí mismo")
    u.is_active = False
    u.deleted_at = utcnow()
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await audit(session, "DELETE", "user", u.id, user=me, ip=client_ip(request))
    return {"detail": "Usuario desactivado"}


@router.post("/{user_id}/reset-password", summary="Genera una contraseña temporal (se muestra una vez)")
async def admin_reset(user_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session), me: User = Depends(admin)):
    u = await _get(session, user_id)
    temp = secrets.token_urlsafe(12)
    u.password_hash = hash_password(temp)
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    await audit(session, "RESET_PASSWORD", "user", u.id, user=me, ip=client_ip(request))
    return {"temporary_password": temp}
