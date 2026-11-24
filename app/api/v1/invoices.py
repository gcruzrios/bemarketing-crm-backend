import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.deps import client_ip, require_capability
from app.core.pagination import PageParams, paginate
from app.core.security import utcnow
from app.models import Invoice, InvoiceItem, InvoiceStatus, User
from app.schemas.crm import InvoiceIn, InvoiceOut, InvoiceUpdate, MarkPaidIn
from app.services.audit import audit, snapshot
from app.services.doc_service import build_items, next_number, resolve_parties
from app.services.pdf_service import render_document

router = APIRouter(prefix="/invoices", tags=["invoices"])
S = InvoiceStatus


async def _get(session, iid, with_pdf: bool = False) -> Invoice:
    i = (await session.execute(select(Invoice).where(Invoice.id == iid, Invoice.deleted_at.is_(None)).options(*([undefer(Invoice.pdf_data)] if with_pdf else [])))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Factura no encontrada")
    return i


def _need(i: Invoice, *allowed):
    if i.status not in allowed:
        raise HTTPException(409, f"Estado actual '{i.status.value}' no permite esta acción")


@router.get("", summary="Listar facturas")
async def list_invoices(params: PageParams = Depends(), status: InvoiceStatus | None = None, company_id: uuid.UUID | None = None,
                        search: str | None = None, session: AsyncSession = Depends(get_session),
                        _: User = Depends(require_capability("invoices:read"))):
    stmt = select(Invoice).where(Invoice.deleted_at.is_(None)).order_by(Invoice.created_at.desc())
    if status:
        stmt = stmt.where(Invoice.status == status)
    if company_id:
        stmt = stmt.where(Invoice.company_id == company_id)
    if search:
        stmt = stmt.where(Invoice.invoice_number.ilike(f"%{search}%"))
    return await paginate(session, stmt, params, InvoiceOut)


@router.post("", response_model=InvoiceOut, status_code=201, summary="Crear factura interna (borrador)")
async def create_invoice(body: InvoiceIn, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("invoices:write"))):
    company_id, contact_id, opp_id = await resolve_parties(session, body.company_id, body.contact_id, body.opportunity_id)
    items, totals = await build_items(session, body.items, InvoiceItem)
    inv = Invoice(invoice_number=await next_number(session, "FAC"), opportunity_id=opp_id, company_id=company_id,
                  contact_id=contact_id, currency=body.currency or settings.DEFAULT_CURRENCY, issue_date=body.issue_date,
                  due_date=body.due_date, notes=body.notes, created_by=user.id, items=items, **totals)
    session.add(inv)
    await session.flush()
    await session.refresh(inv, ["items"])
    await audit(session, "CREATE_INVOICE", "invoice", inv.id, user=user, new=snapshot(inv), ip=client_ip(request))
    return inv


@router.get("/{iid}", response_model=InvoiceOut)
async def get_invoice(iid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("invoices:read"))):
    return await _get(session, iid)


@router.put("/{iid}", response_model=InvoiceOut, summary="Editar (solo borrador)")
async def update_invoice(iid: uuid.UUID, body: InvoiceUpdate, request: Request, session: AsyncSession = Depends(get_session),
                         user: User = Depends(require_capability("invoices:write"))):
    inv = await _get(session, iid)
    _need(inv, S.draft)
    old = snapshot(inv)
    data = body.model_dump(exclude_unset=True, exclude={"items"})
    if "contact_id" in data:
        await resolve_parties(session, inv.company_id, data["contact_id"], None)
    for k, v in data.items():
        if v is None and k == "currency":
            continue
        setattr(inv, k, v)
    if body.items is not None:
        items, totals = await build_items(session, body.items, InvoiceItem)
        inv.items.clear()
        await session.flush()
        inv.items.extend(items)
        for k, v in totals.items():
            setattr(inv, k, v)
    await session.flush()
    await session.refresh(inv, ["items"])
    await audit(session, "UPDATE", "invoice", inv.id, user=user, old=old, new=snapshot(inv), ip=client_ip(request))
    return inv


@router.post("/{iid}/send", response_model=InvoiceOut, summary="Marcar enviada y congelar PDF (no envía correo)")
async def send_invoice(iid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_capability("invoices:write"))):
    inv = await _get(session, iid)
    _need(inv, S.draft, S.issued)
    today = utcnow().date()
    inv.issue_date = inv.issue_date or today
    inv.due_date = inv.due_date or inv.issue_date + timedelta(days=30)
    inv.status = S.sent
    inv.pdf_data = await render_document(session, inv, "invoice")
    inv.pdf_path = f"invoices/{inv.invoice_number}.pdf"
    await audit(session, "SEND_INVOICE", "invoice", inv.id, user=user, ip=client_ip(request))
    return inv


@router.post("/{iid}/mark-paid", response_model=InvoiceOut, summary="Marcar pagada")
async def mark_paid(iid: uuid.UUID, request: Request, body: MarkPaidIn | None = None, session: AsyncSession = Depends(get_session),
                    user: User = Depends(require_capability("invoices:mark_paid"))):
    inv = await _get(session, iid)
    _need(inv, S.issued, S.sent, S.partial, S.overdue)
    inv.status = S.paid
    inv.paid_date = (body.paid_date if body and body.paid_date else utcnow().date())
    await audit(session, "MARK_PAID", "invoice", inv.id, user=user, ip=client_ip(request))
    return inv


@router.post("/{iid}/cancel", response_model=InvoiceOut, summary="Anular")
async def cancel(iid: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session),
                 user: User = Depends(require_capability("invoices:write"))):
    inv = await _get(session, iid)
    _need(inv, S.draft, S.issued, S.sent, S.partial, S.overdue)
    inv.status = S.cancelled
    await audit(session, "CANCEL_INVOICE", "invoice", inv.id, user=user, ip=client_ip(request))
    return inv


@router.get("/{iid}/pdf", summary="Descargar PDF", response_class=Response)
async def pdf(iid: uuid.UUID, session: AsyncSession = Depends(get_session), _: User = Depends(require_capability("invoices:read"))):
    inv = await _get(session, iid, with_pdf=True)
    data = inv.pdf_data if inv.status != S.draft and inv.pdf_data else await render_document(session, inv, "invoice")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{inv.invoice_number}.pdf"'})
