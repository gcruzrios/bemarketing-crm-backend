import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import UserRole
from app.schemas.common import ORM


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeOut(BaseModel):
    user: UserOut
    capabilities: list[str]


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=200)


class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    role: UserRole


class UserUpdate(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    scopes: list[str] = ["leads:ingest", "leads:read", "activities:create"]


class ApiKeyOut(ORM):
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str
