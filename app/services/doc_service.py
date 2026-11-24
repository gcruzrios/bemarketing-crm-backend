"""Lógica compartida de cotizaciones y facturas: totales en backend, numeración atómica, snapshot."""
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Contact, Opportunity, Service

CENT = Decimal("0.01")


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


async def next_number(session: AsyncSession, prefix: str) -> str:
    year = datetime.now().year
    return (await session.execute(text("SELECT next_document_number(:t, :y)"), {"t": prefix, "y": year})).scalar_one()


async def build_items(session: AsyncSession, items: list, item_cls) -> tuple[list, dict]:
    """Construye ítems con snapshot de precio/descripcion y calcula totales."""
    default_rate = Decimal(str(settings.DEFAULT_TAX_RATE))
    out, subtotal, discount, tax = [], Decimal(0), Decimal(0), Decimal(0)
    for pos, it in enumerate(items):
        svc = None
        if it.service_id:
            svc = (await session.execute(
                select(Service).where(Service.id == it.service_id, Service.deleted_at.is_(None)))).scalar_one_or_none()
            if not svc:
                raise HTTPException(422, f"Servicio {it.service_id} no existe")
        unit = it.unit_price if it.unit_price is not None else (svc.price if svc else None)
        desc = it.description or (svc.name if svc else None)
        if unit is None or not desc:
            raise HTTPException(422, "Cada ítem necesita service_id o description + unit_price")
        disc, qty = q2(it.discount), q2(it.quantity)
        gross = q2(qty * unit)
        if disc > gross:
            raise HTTPException(422, "El descuento no puede superar el monto del ítem")
        rate = it.tax_rate if it.tax_rate is not None else default_rate
        base = gross - disc
        item_tax = q2(base * rate)
        out.append(item_cls(position=pos, service_id=it.service_id, description=desc, quantity=qty,
                            unit_price=q2(unit), discount=disc, tax_rate=rate, tax=item_tax, total=base + item_tax))
        subtotal += gross
        discount += disc
        tax += item_tax
    return out, {"subtotal": subtotal, "discount": discount, "tax": tax, "total": subtotal - discount + tax}


async def resolve_parties(session: AsyncSession, company_id, contact_id, opportunity_id):
    """Devuelve (company_id, contact_id, opportunity_id) validados."""
    if opportunity_id:
        opp = (await session.execute(
            select(Opportunity).where(Opportunity.id == opportunity_id, Opportunity.deleted_at.is_(None)))).scalar_one_or_none()
        if not opp:
            raise HTTPException(422, "Oportunidad no existe")
        company_id = company_id or opp.company_id
        contact_id = contact_id or opp.contact_id
        if company_id != opp.company_id:
            raise HTTPException(422, "company_id no coincide con la oportunidad")
    if not company_id:
        raise HTTPException(422, "company_id u opportunity_id es obligatorio")
    if not (await session.execute(select(Company.id).where(Company.id == company_id, Company.deleted_at.is_(None)))).first():
        raise HTTPException(422, "Empresa no existe")
    if contact_id and not (await session.execute(
            select(Contact.id).where(Contact.id == contact_id, Contact.company_id == company_id, Contact.deleted_at.is_(None)))).first():
        raise HTTPException(422, "El contacto no pertenece a la empresa")
    return company_id, contact_id, opportunity_id
