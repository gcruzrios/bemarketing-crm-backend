import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType, CompanyType, Currency, EnrichmentStatus, LeadSource, LeadStatus,
)
from app.schemas.common import ORM


class LeadFields(BaseModel):
    """Campos de ficha editables. Longitudes máximas: el contenido viene de terceros."""
    model_config = ConfigDict(extra="forbid")
    company_name: str | None = Field(None, max_length=255)
    company_type: CompanyType | None = None
    website: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    contact_first_name: str | None = Field(None, max_length=100)
    contact_last_name: str | None = Field(None, max_length=100)
    contact_job_title: str | None = Field(None, max_length=150)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    whatsapp: str | None = Field(None, max_length=50)
    interest_summary: str | None = Field(None, max_length=4000)
    interested_services: list[uuid.UUID] | None = Field(None, max_length=30)
    budget_hint: str | None = Field(None, max_length=100)
    timeline_hint: str | None = Field(None, max_length=100)
    score: int | None = Field(None, ge=0, le=100)


class LeadCreate(LeadFields):
    source: LeadSource = LeadSource.other
    source_reference: str | None = Field(None, max_length=255)
    first_message: str | None = Field(None, max_length=4000)
    assigned_to: uuid.UUID | None = None


class LeadUpdate(LeadFields):
    source: LeadSource | None = None
    source_reference: str | None = Field(None, max_length=255)
    status: LeadStatus | None = None


class LeadOut(ORM):
    id: uuid.UUID
    company_name: str | None
    company_type: CompanyType | None
    website: str | None
    city: str | None
    contact_first_name: str | None
    contact_last_name: str | None
    contact_job_title: str | None
    email: str | None
    phone: str | None
    whatsapp: str | None
    source: LeadSource
    source_reference: str | None
    first_message: str | None
    interest_summary: str | None
    interested_services: list[uuid.UUID] | None
    budget_hint: str | None
    timeline_hint: str | None
    status: LeadStatus
    score: int | None
    assigned_to: uuid.UUID | None
    enrichment_status: EnrichmentStatus
    completeness: int
    manual_fields: list[str]
    field_meta: dict
    created_at: datetime
    updated_at: datetime
    converted_at: datetime | None


class AssignIn(BaseModel):
    assigned_to: uuid.UUID | None


class ResolveReviewIn(BaseModel):
    note: str | None = Field(None, max_length=1000)
    accept_fields: dict | None = None  # valores finales que la persona confirma


class ConvertIn(BaseModel):
    company_id: uuid.UUID | None = None
    force_create_company: bool = False
    create_opportunity: bool = True
    opportunity_name: str | None = Field(None, max_length=255)
    expected_value: float | None = Field(None, ge=0)
    currency: Currency | None = None
    tax_id: str | None = Field(None, max_length=50)
    legal_name: str | None = Field(None, max_length=255)


class LeadEventOut(ORM):
    id: uuid.UUID
    lead_id: uuid.UUID
    actor_type: ActorType
    actor_id: uuid.UUID | None
    event_type: str
    changes: dict | None
    note: str | None
    created_at: datetime


# ── Agente ──
class AgentUpsertIn(BaseModel):
    source: LeadSource
    source_reference: str | None = Field(None, max_length=255)
    first_message: str | None = Field(None, max_length=4000)
    fields: LeadFields
    confidence: dict[str, float] = {}
    agent_note: str | None = Field(None, max_length=2000)


class AgentActivityIn(BaseModel):
    type: str = "follow_up"
    subject: str = Field(max_length=255)
    description: str | None = Field(None, max_length=4000)
    due_date: datetime | None = None
