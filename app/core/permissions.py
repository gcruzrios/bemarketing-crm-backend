"""Única fuente de verdad de la matriz rol → capacidades (§6)."""
from app.models.enums import UserRole

ALL = {
    "leads:read", "leads:write", "leads:convert", "leads:assign",
    "companies:read", "companies:write",
    "contacts:read", "contacts:write",
    "opportunities:read", "opportunities:write",
    "pipeline:move", "pipeline:configure",
    "quotes:read", "quotes:write", "quotes:send",
    "invoices:read", "invoices:write", "invoices:mark_paid",
    "activities:read", "activities:write",
    "services:read", "services:write",
    "users:manage", "apikeys:manage", "audit:read", "dashboard:read",
}

ROLE_CAPABILITIES: dict[UserRole, set[str]] = {
    UserRole.admin: set(ALL),
    UserRole.manager: ALL - {"users:manage", "apikeys:manage"},
    UserRole.sales: {
        "leads:read", "leads:write", "leads:convert", "leads:assign",
        "companies:read", "companies:write",
        "contacts:read", "contacts:write",
        "opportunities:read", "opportunities:write",
        "pipeline:move",
        "quotes:read", "quotes:write", "quotes:send",
        "invoices:read",
        "activities:read", "activities:write",
        "services:read",
        "dashboard:read",
    },
    UserRole.accounting: {
        "companies:read", "contacts:read",
        "opportunities:read",
        "quotes:read",
        "invoices:read", "invoices:write", "invoices:mark_paid",
        "services:read",
        "dashboard:read",
    },
}

# Scopes de API key (solo /agent/*)
AGENT_SCOPES = {"leads:ingest", "leads:read", "activities:create"}


def capabilities_for(role: UserRole) -> list[str]:
    return sorted(ROLE_CAPABILITIES[role])
