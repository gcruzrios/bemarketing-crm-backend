import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    LargeBinary, Numeric, SmallInteger, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.models.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, pg_enum
from app.models.enums import (
    ActivityStatus, ActivityType, ActorType, BillingType, CompanyType, Currency, EnrichmentStatus,
    IngestStatus, InvoiceStatus, LeadSource, LeadStatus, OpportunityStatus, QuoteStatus,
    ServiceType, UserRole,
)

UUIDFK = UUID(as_uuid=True)
Money = Numeric(12, 2)
NOW = func.now()


def tz_col(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


# ───────────── Auth ─────────────
class User(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    __table_args__ = (
        Index("uq_users_email_active", text("lower(email)"), unique=True, postgresql_where=text("deleted_at IS NULL")),
    )


class RefreshToken(IdMixin, Base):
    __tablename__ = "refresh_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = tz_col()
    revoked_at: Mapped[datetime | None] = tz_col(nullable=True)
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class PasswordResetToken(IdMixin, Base):
    __tablename__ = "password_reset_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = tz_col()
    used_at: Mapped[datetime | None] = tz_col(nullable=True)
    created_at: Mapped[datetime] = tz_col(server_default=NOW)


class ApiKey(IdMixin, Base):
    __tablename__ = "api_keys"
    name: Mapped[str] = mapped_column(String(150))
    key_prefix: Mapped[str] = mapped_column(String(20), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_used_at: Mapped[datetime | None] = tz_col(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    revoked_at: Mapped[datetime | None] = tz_col(nullable=True)


# ───────────── Leads ─────────────
class Lead(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "leads"
    company_name: Mapped[str | None] = mapped_column(String(255))
    company_type: Mapped[CompanyType | None] = mapped_column(pg_enum(CompanyType))
    website: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))

    contact_first_name: Mapped[str | None] = mapped_column(String(100))
    contact_last_name: Mapped[str | None] = mapped_column(String(100))
    contact_job_title: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    whatsapp: Mapped[str | None] = mapped_column(String(50))

    source: Mapped[LeadSource] = mapped_column(pg_enum(LeadSource), server_default="other")
    source_reference: Mapped[str | None] = mapped_column(String(255))
    first_message: Mapped[str | None] = mapped_column(Text)

    interest_summary: Mapped[str | None] = mapped_column(Text)
    interested_services: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUIDFK))
    budget_hint: Mapped[str | None] = mapped_column(String(100))
    timeline_hint: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[LeadStatus] = mapped_column(pg_enum(LeadStatus), server_default="new")
    score: Mapped[int | None] = mapped_column(Integer)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))

    enrichment_status: Mapped[EnrichmentStatus] = mapped_column(
        pg_enum(EnrichmentStatus), server_default="incomplete"
    )
    completeness: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    manual_fields: Mapped[list[str]] = mapped_column(ARRAY(String), server_default=text("'{}'"))
    field_meta: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    converted_at: Mapped[datetime | None] = tz_col(nullable=True)

    __table_args__ = (
        Index("ix_leads_email", "email"),
        Index("ix_leads_phone", "phone"),
        Index("ix_leads_whatsapp", "whatsapp"),
        Index("ix_leads_status", "status"),
        Index("ix_leads_source", "source"),
        Index("ix_leads_assigned_to", "assigned_to"),
        Index("ix_leads_company_trgm", "company_name", postgresql_using="gin", postgresql_ops={"company_name": "gin_trgm_ops"}),
        Index("ix_leads_first_trgm", "contact_first_name", postgresql_using="gin", postgresql_ops={"contact_first_name": "gin_trgm_ops"}),
        Index("ix_leads_last_trgm", "contact_last_name", postgresql_using="gin", postgresql_ops={"contact_last_name": "gin_trgm_ops"}),
        Index("ix_leads_email_trgm", "email", postgresql_using="gin", postgresql_ops={"email": "gin_trgm_ops"}),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_leads_score"),
    )


class LeadEvent(IdMixin, Base):
    __tablename__ = "lead_events"
    lead_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK)
    event_type: Mapped[str] = mapped_column(String(50))
    changes: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = tz_col(server_default=NOW)


class IngestEvent(IdMixin, Base):
    __tablename__ = "ingest_events"
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("api_keys.id"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSONB)
    response: Mapped[dict | None] = mapped_column(JSONB)
    result_lead_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK)
    status: Mapped[IngestStatus] = mapped_column(pg_enum(IngestStatus))
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    __table_args__ = (UniqueConstraint("api_key_id", "idempotency_key", name="uq_ingest_key"),)


# ───────────── Companies / Contacts / Services ─────────────
class Company(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "companies"
    name: Mapped[str] = mapped_column(String(255))
    legal_name: Mapped[str | None] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(50))
    company_type: Mapped[CompanyType] = mapped_column(pg_enum(CompanyType), server_default="other")
    industry: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100), server_default="Costa Rica")
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("uq_companies_tax_id", "tax_id", unique=True, postgresql_where=text("deleted_at IS NULL AND tax_id IS NOT NULL")),
        Index("ix_companies_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )


class Contact(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "contacts"
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    whatsapp: Mapped[str | None] = mapped_column(String(50))
    job_title: Mapped[str | None] = mapped_column(String(150))
    company_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("companies.id"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("leads.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_contacts_company", "company_id"),
        Index("ix_contacts_email", "email"),
        Index("ix_contacts_first_trgm", "first_name", postgresql_using="gin", postgresql_ops={"first_name": "gin_trgm_ops"}),
        Index("ix_contacts_last_trgm", "last_name", postgresql_using="gin", postgresql_ops={"last_name": "gin_trgm_ops"}),
    )


class Service(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "services"
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    service_type: Mapped[ServiceType] = mapped_column(pg_enum(ServiceType), server_default="other")
    price: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    currency: Mapped[Currency] = mapped_column(pg_enum(Currency), server_default="USD")
    billing_type: Mapped[BillingType] = mapped_column(pg_enum(BillingType), server_default="one_time")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    __table_args__ = (
        Index("uq_services_code", "code", unique=True, postgresql_where=text("deleted_at IS NULL AND code IS NOT NULL")),
    )


# ───────────── Pipeline / Opportunities ─────────────
class PipelineStage(IdMixin, Base):
    __tablename__ = "pipeline_stages"
    name: Mapped[str] = mapped_column(String(100))
    stage_order: Mapped[int] = mapped_column(Integer)
    probability: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    color: Mapped[str | None] = mapped_column(String(20))
    is_won: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_lost: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    __table_args__ = (
        Index("uq_stage_won", "is_won", unique=True, postgresql_where=text("is_won")),
        Index("uq_stage_lost", "is_lost", unique=True, postgresql_where=text("is_lost")),
        CheckConstraint("NOT (is_won AND is_lost)", name="ck_stage_won_lost"),
        CheckConstraint("probability BETWEEN 0 AND 100", name="ck_stage_probability"),
    )


class Opportunity(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "opportunities"
    name: Mapped[str] = mapped_column(String(255))
    stage_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("pipeline_stages.id"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("leads.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("companies.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("contacts.id"))
    expected_value: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[Currency] = mapped_column(pg_enum(Currency), server_default="USD")
    probability: Mapped[int | None] = mapped_column(SmallInteger)
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[OpportunityStatus] = mapped_column(pg_enum(OpportunityStatus), server_default="open")
    lost_reason: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = tz_col(nullable=True)
    stage: Mapped[PipelineStage] = relationship(lazy="joined")
    __table_args__ = (
        Index("ix_opp_status", "status"),
        Index("ix_opp_stage", "stage_id"),
        Index("ix_opp_company", "company_id"),
    )


class OpportunityService(IdMixin, Base):
    __tablename__ = "opportunity_services"
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("services.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default=text("1"))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Money)


# ───────────── Quotes / Invoices ─────────────
class Quote(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "quotes"
    quote_number: Mapped[str] = mapped_column(String(30), unique=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("opportunities.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("companies.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("contacts.id"))
    status: Mapped[QuoteStatus] = mapped_column(pg_enum(QuoteStatus), server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    discount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    tax: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    currency: Mapped[Currency] = mapped_column(pg_enum(Currency), server_default="USD")
    valid_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(255))
    pdf_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))  # filesystem efímero en Heroku
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    sent_at: Mapped[datetime | None] = tz_col(nullable=True)
    accepted_at: Mapped[datetime | None] = tz_col(nullable=True)
    items: Mapped[list["QuoteItem"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="QuoteItem.position"
    )
    __table_args__ = (
        CheckConstraint("accepted_at IS NULL OR status = 'accepted'", name="ck_quote_accepted_at"),
        Index("ix_quotes_status", "status"),
    )


class QuoteItem(IdMixin, Base):
    __tablename__ = "quote_items"
    quote_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("services.id"))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0"))
    tax: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Money)


class Invoice(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "invoices"
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("quotes.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("opportunities.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("companies.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("contacts.id"))
    status: Mapped[InvoiceStatus] = mapped_column(pg_enum(InvoiceStatus), server_default="draft")
    subtotal: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    discount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    tax: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    currency: Mapped[Currency] = mapped_column(pg_enum(Currency), server_default="USD")
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    paid_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(255))
    pdf_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    items: Mapped[list["InvoiceItem"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="InvoiceItem.position"
    )
    __table_args__ = (Index("ix_invoices_status", "status"),)


class InvoiceItem(IdMixin, Base):
    __tablename__ = "invoice_items"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("services.id"))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0"))
    tax: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Money)


# ───────────── Activities / Notifications / Audit ─────────────
class Activity(IdMixin, SoftDeleteMixin, Base):
    __tablename__ = "activities"
    type: Mapped[ActivityType] = mapped_column(pg_enum(ActivityType))
    subject: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("leads.id"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("contacts.id"))
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("companies.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("opportunities.id"))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    status: Mapped[ActivityStatus] = mapped_column(pg_enum(ActivityStatus), server_default="pending")
    due_date: Mapped[datetime | None] = tz_col(nullable=True)
    completed_at: Mapped[datetime | None] = tz_col(nullable=True)
    created_by_agent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    __table_args__ = (Index("ix_activities_due", "due_date"),)


class Notification(IdMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDFK, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK)
    read_at: Mapped[datetime | None] = tz_col(nullable=True)
    created_at: Mapped[datetime] = tz_col(server_default=NOW)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("users.id"))
    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK, ForeignKey("api_keys.id"))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUIDFK)
    old_data: Mapped[dict | None] = mapped_column(JSONB)
    new_data: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = tz_col(server_default=NOW)
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)


class DocumentCounter(Base):
    __tablename__ = "document_counters"
    doc_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, server_default=text("0"))
