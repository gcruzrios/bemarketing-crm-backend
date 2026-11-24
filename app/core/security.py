import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.core.config import settings

_ph = PasswordHasher()  # argon2id por defecto


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except VerificationError:
        return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, role: str) -> str:
    exp = utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "role": role, "exp": exp, "type": "access"},
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict | None:
    try:
        data = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return data if data.get("type") == "access" else None


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def generate_api_key() -> tuple[str, str, str]:
    """Devuelve (key_en_claro, prefijo, hash). La key en claro se muestra una sola vez."""
    prefix = "bmk_" + secrets.token_hex(4)
    key = f"{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, sha256(key)
