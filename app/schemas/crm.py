import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    ActivityStatus, ActivityType, BillingType, CompanyType, Currency, InvoiceStatus,
    OpportunityStatus, QuoteStatus, ServiceType,
)
from app.schemas.common import ORM


# ── Companies ──
class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    legal_name: str | None = Field(None, max_length=255)
    tax_id: str | None = Field(None, max_length=50)
    company_type: CompanyType = CompanyType.other
    industry: str | None = Field(None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    website: str | None = Field(None, max_length=255)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=4000)


class CompanyUpdate(CompanyIn):
    name: str | None = Field(None, min_length=1, max_length=255)
    company_type: CompanyType | None = None


class CompanyOut(ORM, CompanyIn):
    id: uuid.UUID
    email: str | None = None
    created_at: datetime
    updated_at: datetime
    possible_duplicate: dict | None = None


# ── Contacts ──
class ContactIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    whatsapp: str | None = Field(None, max_length=50)
    job_title: str | None = Field(None, max_length=150)
    company_id: uuid.UUID
    notes: str | None = Field(None, max_length=4000)


class ContactUpdate(ContactIn):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    company_id: uuid.UUID | None = None


class ContactOut(ORM):
    id: uuid.UUID
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    whatsapp: str | None
    job_title: str | None
    company_id: uuid.UUID
    lead_id: uuid.UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    possible_duplicate: dict | None = None


# ── Services ──
class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(None, max_length=50)
    description: str | None = Field(None, max_length=4000)
    service_type: ServiceType = ServiceType.other
    price: Decimal = Field(Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    currency: Currency = Currency.USD
    billing_type: BillingType = BillingType.one_time
    is_active: bool = True


class ServiceUpdate(ServiceIn):
    name: str | None = Field(None, min_length=1, max_length=200)
    service_type: ServiceType | None = None
    price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    currency: Currency | None = None
    billing_type: BillingType | None = None
    is_active: bool | None = None


class ServiceOut(ORM, ServiceIn):
    id: uuid.UUID
    created_at: datetime


# ── Pipeline ──
class StageIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    stage_order: int | None = None
    probability: int = Field(0, ge=0, le=100)
    color: str | None = Field(None, max_length=20)
    is_won: bool = False
    is_lost: bool = False


class StageUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    probability: int | None = Field(None, ge=0, le=100)
    color: str | None = Field(None, max_length=20)


class StageOut(ORM):
    id: uuid.UUID
    name: str
    stage_order: int
    probability: int
    color: str | None
    is_won: bool
    is_lost: bool


class ReorderItem(BaseModel):
    id: uuid.UUID
    stage_order: int


# ── Opportunities ──
class OpportunityServiceIn(BaseModel):
    service_id: uuid.UUID
    quantity: Decimal = Field(Decimal("1"), gt=0)
    unit_price: Decimal | None = Field(None, ge=0)  # por defecto el precio del catálogo (snapshot)
    discount: Decimal = Field(Decimal("0"), ge=0)


class OpportunityServiceOut(ORM):
    id: uuid.UUID
    service_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    total: Decimal


class OpportunityIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    stage_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    company_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    expected_value: Decimal | None = Field(None, ge=0)
    currency: Currency = Currency.USD
    probability: int | None = Field(None, ge=0, le=100)
    expected_close_date: date | None = None
    assigned_to: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=4000)
    services: list[OpportunityServiceIn] | None = None


class OpportunityUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    contact_id: uuid.UUID | None = None
    expected_value: Decimal | None = Field(None, ge=0)
    currency: Currency | None = None
    probability: int | None = Field(None, ge=0, le=100)
    expected_close_date: date | None = None
    assigned_to: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=4000)
    services: list[OpportunityServiceIn] | None = None


class OpportunityOut(ORM):
    id: uuid.UUID
    name: str
    stage_id: uuid.UUID
    lead_id: uuid.UUID | None
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    expected_value: Decimal | None
    currency: Currency
    probability: int | None
    expected_close_date: date | None
    status: OpportunityStatus
    lost_reason: str | None
    assigned_to: uuid.UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class MoveIn(BaseModel):
    stage_id: uuid.UUID


class LostIn(BaseModel):
    lost_reason: str = Field(min_length=1, max_length=1000)


# ── Quotes / Invoices ──
class ItemIn(BaseModel):
    service_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=500)
    quantity: Decimal = Field(Decimal("1"), gt=0, max_digits=10, decimal_places=2)
    unit_price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    discount: Decimal = Field(Decimal("0"), ge=0, max_digits=12, decimal_places=2)  # monto
    tax_rate: Decimal | None = Field(None, ge=0, le=1, max_digits=5, decimal_places=4)


class ItemOut(ORM):
    id: uuid.UUID
    position: int
    service_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    tax_rate: Decimal
    tax: Decimal
    total: Decimal


class QuoteIn(BaseModel):
    opportunity_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    currency: Currency | None = None
    valid_until: date | None = None
    notes: str | None = Field(None, max_length=4000)
    items: list[ItemIn] = Field(min_length=1, max_length=100)


class QuoteUpdate(BaseModel):
    contact_id: uuid.UUID | None = None
    currency: Currency | None = None
    valid_until: date | None = None
    notes: str | None = Field(None, max_length=4000)
    items: list[ItemIn] | None = Field(None, min_length=1, max_length=100)


class QuoteOut(ORM):
    id: uuid.UUID
    quote_number: str
    opportunity_id: uuid.UUID | None
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    status: QuoteStatus
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    currency: Currency
    valid_until: date | None
    notes: str | None
    pdf_path: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    sent_at: datetime | None
    accepted_at: datetime | None
    items: list[ItemOut]


class InvoiceIn(BaseModel):
    opportunity_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    currency: Currency | None = None
    issue_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(None, max_length=4000)
    items: list[ItemIn] = Field(min_length=1, max_length=100)


class InvoiceUpdate(BaseModel):
    contact_id: uuid.UUID | None = None
    currency: Currency | None = None
    issue_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(None, max_length=4000)
    items: list[ItemIn] | None = Field(None, min_length=1, max_length=100)


class InvoiceOut(ORM):
    id: uuid.UUID
    invoice_number: str
    quote_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    status: InvoiceStatus
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    currency: Currency
    issue_date: date | None
    due_date: date | None
    paid_date: date | None
    notes: str | None
    pdf_path: str | None
    created_at: datetime
    items: list[ItemOut]


class MarkPaidIn(BaseModel):
    paid_date: date | None = None


# ── Activities / Notifications ──
class ActivityIn(BaseModel):
    type: ActivityType
    subject: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    lead_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    due_date: datetime | None = None


class ActivityUpdate(BaseModel):
    type: ActivityType | None = None
    subject: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    assigned_to: uuid.UUID | None = None
    due_date: datetime | None = None
    status: ActivityStatus | None = None


class ActivityOut(ORM):
    id: uuid.UUID
    type: ActivityType
    subject: str
    description: str | None
    lead_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    company_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    assigned_to: uuid.UUID | None
    status: ActivityStatus
    due_date: datetime | None
    completed_at: datetime | None
    created_by_agent: bool
    created_at: datetime


class NotificationOut(ORM):
    id: uuid.UUID
    type: str
    title: str
    body: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class AuditOut(ORM):
    id: int
    user_id: uuid.UUID | None
    actor_type: str
    api_key_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    old_data: dict | None
    new_data: dict | None
    ip_address: str | None
    created_at: datetime
