import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.deps import client_ip, current_user
from app.core.limiter import limiter
from app.core.permissions import capabilities_for
from app.core.security import (
    create_access_token, hash_password, new_opaque_token, sha256, utcnow, verify_password,
)
from app.models import PasswordResetToken, RefreshToken, User
from app.schemas.auth import ForgotIn, LoginIn, LoginOut, MeOut, ResetIn, UserOut
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("auth")
COOKIE = "refresh_token"
# hash ficticio para igualar tiempos cuando el usuario no existe
_DUMMY = hash_password("dummy-password")


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(
        COOKIE, token, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, httponly=True,
        secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE, path="/api/v1/auth",
    )


async def _issue(session, user: User, request: Request, resp: Response) -> str:
    raw = new_opaque_token()
    session.add(RefreshToken(
        user_id=user.id, token_hash=sha256(raw), user_agent=(request.headers.get("user-agent") or "")[:500],
        ip_address=client_ip(request), expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    ))
    _set_cookie(resp, raw)
    return create_access_token(str(user.id), user.role.value)


@router.post("/login", response_model=LoginOut, summary="Iniciar sesión",
             description="Devuelve access token (15 min) y setea cookie httpOnly con el refresh token.")
@limiter.limit("5/minute")
async def login(request: Request, response: Response, body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(
        func.lower(User.email) == body.email.lower(), User.deleted_at.is_(None)))).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else _DUMMY)
    if not user or not ok or not user.is_active:
        raise HTTPException(401, "Credenciales inválidas")
    token = await _issue(session, user, request, response)
    await audit(session, "LOGIN", "user", user.id, user=user, ip=client_ip(request))
    return LoginOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/refresh", summary="Rotar refresh token")
@limiter.limit("30/minute")
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "Sin refresh token")
    rt = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256(raw)))).scalar_one_or_none()
    if not rt:
        raise HTTPException(401, "Refresh token inválido")
    if rt.revoked_at is not None:
        # reutilización de un token ya rotado: posible robo → revocar todos
        await session.execute(update(RefreshToken).where(RefreshToken.user_id == rt.user_id, RefreshToken.revoked_at.is_(None))
                              .values(revoked_at=utcnow()))
        await session.commit()
        raise HTTPException(401, "Refresh token revocado")
    if rt.expires_at < utcnow():
        raise HTTPException(401, "Refresh token expirado")
    user = (await session.execute(select(User).where(User.id == rt.user_id, User.deleted_at.is_(None)))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "Usuario inactivo")
    rt.revoked_at = utcnow()
    token = await _issue(session, user, request, response)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout", summary="Cerrar sesión")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(COOKIE)
    if raw:
        await session.execute(update(RefreshToken).where(RefreshToken.token_hash == sha256(raw), RefreshToken.revoked_at.is_(None))
                              .values(revoked_at=utcnow()))
    response.delete_cookie(COOKIE, path="/api/v1/auth")
    return {"detail": "ok"}


@router.get("/me", response_model=MeOut, summary="Usuario actual y capacidades")
async def me(user: User = Depends(current_user)):
    return MeOut(user=UserOut.model_validate(user), capabilities=capabilities_for(user.role))


@router.post("/forgot-password", summary="Solicitar restablecimiento",
             description="El CRM no envía correo. En desarrollo devuelve el token; en producción se registra en logs "
                         "y un admin puede usar POST /users/{id}/reset-password.")
@limiter.limit("3/minute")
async def forgot_password(request: Request, body: ForgotIn, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(
        func.lower(User.email) == body.email.lower(), User.deleted_at.is_(None), User.is_active.is_(True)))).scalar_one_or_none()
    out: dict = {"detail": "Si el correo existe, se generó un token de restablecimiento"}
    if user:
        raw = new_opaque_token()
        session.add(PasswordResetToken(user_id=user.id, token_hash=sha256(raw), expires_at=utcnow() + timedelta(minutes=30)))
        if settings.is_dev:
            out["token"] = raw
        else:
            log.warning("Token de reset generado para user_id=%s (entregar por canal seguro)", user.id)
    return out


@router.post("/reset-password", summary="Restablecer contraseña con token de un solo uso")
@limiter.limit("5/minute")
async def reset_password(request: Request, body: ResetIn, session: AsyncSession = Depends(get_session)):
    t = (await session.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == sha256(body.token)))).scalar_one_or_none()
    if not t or t.used_at or t.expires_at < utcnow():
        raise HTTPException(400, "Token inválido o expirado")
    user = (await session.execute(select(User).where(User.id == t.user_id, User.deleted_at.is_(None)))).scalar_one_or_none()
    if not user:
        raise HTTPException(400, "Token inválido o expirado")
    user.password_hash = hash_password(body.new_password)
    t.used_at = utcnow()
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                          .values(revoked_at=utcnow()))
    await audit(session, "RESET_PASSWORD", "user", user.id, user=user, ip=client_ip(request))
    return {"detail": "Contraseña actualizada"}
