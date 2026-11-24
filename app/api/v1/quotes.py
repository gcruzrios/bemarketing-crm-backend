import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Invoice, InvoiceItem, InvoiceStatus, Quote, QuoteItem, QuoteStatus, User
from app.schemas.crm import InvoiceOut, QuoteIn, QuoteOut, QuoteUpdate
from app.services.audit import audit, snapshot
from app.services.doc_service import build_items, next_number, resolve_parties
from app.services.notification_service import notify
from app.services.pdf_service import render_document

router = APIRouter(prefix="/quotes", tags=["quotes"])


async def _get(session, qid, with_pdf: bool = False) -> Quote:
    q = (await session.execute(select(Quote).where(Quote.id == qid, Quote.deleted_at.is_(None)).options(*([undefer(Quote.pdf_data)] if with_pdf else [])))).scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Cotización no encontrada")
    return q


def _need(q: Quote, *allowed: QuoteStatus):
    if q.status not in allowed:
        raise HTTPException(409, f"Estado actual '{q.status.value}' no permite esta acción")


@router.get("", summary="Listar cotizaciones")
async def list_quotes(params: PageParams = Depends(), status: QuoteStatus | None = None, company_id: uuid.UUID | None = None,
                      opportunity_id: uuid.UUID | None = None, search: str | None = None,
                      session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("quotes:read"))):
    stmt = select(Quote).where(Quote.deleted_at.is_(None)).order_by(Quote.created_at.desc())
    for col, v in ((Quote.status, status), (Quote.company_id, company_id), (Quote.opportunity_id, opportunity_id)):
        if v is not None:
            stmt = stmt.where(col == v)
    if search:
        stmt = stmt.where(Quote.quote_number.ilike(f"%{search}%"))
    return await paginate(session, stmt, params, QuoteOut)


@router.post("", response_model=QuoteOut, status_code=201, summary="Crear cotización (borrador)",
             description="Totales calculados en el backend; ítems con snapshot de precio. IVA por ítem (default 13%).")
async def create_quote(body: QuoteIn, request: Request, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_capability("quotes:write"))):
    company_id, contact_id, opp_id = await resolve_parties(session, body.company_id, body.contact_id, body.opportunity_id)
    items, totals = await build_items(session, body.items, QuoteItem)
    q = Quote(quote_number=await next_number(session, "COT"), opportunity_id=opp_id, company_id=company_id,
              contact_id=contact_id, currency=body.currency or settings.DEFAULT_CURRENCY, valid_until=body.valid_until,
              notes=body.notes, created_by=user.id, items=items, **totals)
    session.add(q)
    await session.flush()
    await session.refresh(q, ["items"])
    await audit(session, "CREATE", "quote", q.id, user=user, new=snapshot(q), ip=client_ip(request))
    return q


@router.get("/{qid}", response_model=QuoteOut)
async def get_quote(qid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("quotes:read"))):
    return await _get(session, qid)


@router.put("/{qid}", response_model=QuoteOut, summary="Editar (solo borrador)")
async def update_quote(qid: uuid.UUID, body: QuoteUpdate, request: Request, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_capability("quotes:write"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.draft)
    old = snapshot(q)
    data = body.model_dump(exclude_unset=True, exclude={"items"})
    if "contact_id" in data:
        await resolve_parties(session, q.company_id, data["contact_id"], None)
    for k, v in data.items():
        if v is None and k == "currency":
            continue
        setattr(q, k, v)
    if body.items is not None:
        items, totals = await build_items(session, body.items, QuoteItem)
        q.items.clear()
        await session.flush()
        q.items.extend(items)
        for k, v in totals.items():
            setattr(q, k, v)
    await session.flush()
    await session.refresh(q, ["items"])
    await audit(session, "UPDATE", "quote", q.id, user=user, old=old, new=snapshot(q), ip=client_ip(request))
    return q


@router.delete("/{qid}", status_code=204, summary="Soft delete (solo borrador)")
async def delete_quote(qid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_capability("quotes:write"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.draft, QuoteStatus.cancelled)
    q.deleted_at = utcnow()
    await audit(session, "DELETE", "quote", q.id, user=user, old=snapshot(q), ip=client_ip(request))


@router.post("/{qid}/send", response_model=QuoteOut, summary="Marcar enviada y congelar PDF",
             description="No envía correo: cambia estado a `sent`, genera y guarda una copia inmutable del PDF.")
async def send_quote(qid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_capability("quotes:send"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.draft)
    q.status, q.sent_at = QuoteStatus.sent, utcnow()
    q.pdf_data = await render_document(session, q, "quote")
    q.pdf_path = f"quotes/{q.quote_number}.pdf"
    await audit(session, "SEND_QUOTE", "quote", q.id, user=user, ip=client_ip(request))
    return q


@router.post("/{qid}/accept", response_model=QuoteOut, summary="Marcar aceptada")
async def accept(qid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("quotes:send"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.sent, QuoteStatus.viewed)
    q.status, q.accepted_at = QuoteStatus.accepted, utcnow()
    await audit(session, "ACCEPT_QUOTE", "quote", q.id, user=user, ip=client_ip(request))
    if q.created_by and q.created_by != user.id:
        await notify(session, q.created_by, "quote_accepted", f"Cotización {q.quote_number} aceptada", None, "quote", q.id)
    return q


@router.post("/{qid}/reject", response_model=QuoteOut, summary="Marcar rechazada")
async def reject(qid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("quotes:send"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.sent, QuoteStatus.viewed)
    q.status = QuoteStatus.rejected
    await audit(session, "REJECT_QUOTE", "quote", q.id, user=user, ip=client_ip(request))
    return q


@router.post("/{qid}/convert-to-invoice", response_model=InvoiceOut, status_code=201, summary="Convertir a factura (solo aceptadas)")
async def to_invoice(qid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                     user: User = Depends(require_capability("invoices:write"))):
    q = await _get(session, qid)
    _need(q, QuoteStatus.accepted)
    existing = (await session.execute(select(Invoice).where(
        Invoice.quote_id == q.id, Invoice.deleted_at.is_(None), Invoice.status != InvoiceStatus.cancelled))).scalars().first()
    if existing:
        raise HTTPException(409, {"message": "La cotización ya tiene factura", "invoice_id": str(existing.id)})
    inv = Invoice(
        invoice_number=await next_number(session, "FAC"), quote_id=q.id, opportunity_id=q.opportunity_id,
        company_id=q.company_id, contact_id=q.contact_id, currency=q.currency, notes=q.notes, created_by=user.id,
        subtotal=q.subtotal, discount=q.discount, tax=q.tax, total=q.total,
        items=[InvoiceItem(position=i.position, service_id=i.service_id, description=i.description, quantity=i.quantity,
                           unit_price=i.unit_price, discount=i.discount, tax_rate=i.tax_rate, tax=i.tax, total=i.total)
               for i in q.items],
    )
    session.add(inv)
    await session.flush()
    await session.refresh(inv, ["items"])
    await audit(session, "CREATE_INVOICE", "invoice", inv.id, user=user, new=snapshot(inv), ip=client_ip(request))
    return inv


@router.get("/{qid}/pdf", summary="Descargar PDF", response_class=Response)
async def pdf(qid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("quotes:read"))):
    q = await _get(session, qid, with_pdf=True)
    data = q.pdf_data if q.status != QuoteStatus.draft and q.pdf_data else await render_document(session, q, "quote")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.quote_number}.pdf"'})
