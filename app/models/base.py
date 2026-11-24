import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Trae defaults de servidor (created_at, updated_at) con RETURNING: evita lazy-load en async
    __mapper_args__ = {"eager_defaults": True}


def pg_enum(enum_cls: type[enum.Enum], name: str | None = None) -> Enum:
    return Enum(
        enum_cls,
        name=name or enum_cls.__name__.lower(),
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
