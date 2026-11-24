from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import current_user
from app.core.permissions import ROLE_CAPABILITIES
from app.models import Company, Contact, Invoice, Lead, Quote, User

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", summary="Búsqueda global", description="Leads, empresas, contactos y números de cotización/factura según permisos.")
async def search(q: str = Query(min_length=2, max_length=100), limit: int = Query(5, ge=1, le=20),
                 session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    caps = ROLE_CAPABILITIES[user.role]
    like = f"%{q}%"
    out: dict = {}
    if "leads:read" in caps:
        rows = (await session.execute(select(Lead).where(Lead.deleted_at.is_(None), or_(
            Lead.company_name.ilike(like), Lead.contact_first_name.ilike(like), Lead.contact_last_name.ilike(like),
            Lead.email.ilike(like))).limit(limit))).scalars().all()
        out["leads"] = [{"id": r.id, "label": r.company_name or r.contact_first_name, "status": r.status} for r in rows]
    if "companies:read" in caps:
        rows = (await session.execute(select(Company).where(Company.deleted_at.is_(None), or_(
            Company.name.ilike(like), Company.legal_name.ilike(like), Company.tax_id.ilike(like))).limit(limit))).scalars().all()
        out["companies"] = [{"id": r.id, "label": r.name} for r in rows]
    if "contacts:read" in caps:
        rows = (await session.execute(select(Contact).where(Contact.deleted_at.is_(None), or_(
            Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like))).limit(limit))).scalars().all()
        out["contacts"] = [{"id": r.id, "label": f"{r.first_name} {r.last_name or ''}".strip(), "company_id": r.company_id} for r in rows]
    if "quotes:read" in caps:
        rows = (await session.execute(select(Quote).where(Quote.deleted_at.is_(None), Quote.quote_number.ilike(like)).limit(limit))).scalars().all()
        out["quotes"] = [{"id": r.id, "label": r.quote_number, "status": r.status} for r in rows]
    if "invoices:read" in caps:
        rows = (await session.execute(select(Invoice).where(Invoice.deleted_at.is_(None), Invoice.invoice_number.ilike(like)).limit(limit))).scalars().all()
        out["invoices"] = [{"id": r.id, "label": r.invoice_number, "status": r.status} for r in rows]
    return out
