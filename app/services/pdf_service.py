from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Contact

CSS = """
@page { size: A4; margin: 18mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1e293b; }
h1 { font-size: 20pt; margin: 0; color: #0f172a; } .muted { color: #64748b; }
table { width: 100%; border-collapse: collapse; margin-top: 14px; }
th { text-align: left; background: #f1f5f9; padding: 6px; font-size: 9pt; }
td { padding: 6px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
.r { text-align: right; } .totals td { border: 0; } .total td { font-weight: bold; font-size: 12pt; }
.note { margin-top: 18px; font-size: 8.5pt; color: #64748b; }
"""


def _money(v, cur) -> str:
    return f"{cur} {v:,.2f}"


async def render_document(session: AsyncSession, doc, kind: str) -> bytes:
    """kind: 'quote' | 'invoice'. Se importa WeasyPrint de forma perezosa (carga libs nativas)."""
    from weasyprint import HTML

    company = (await session.execute(select(Company).where(Company.id == doc.company_id))).scalar_one()
    contact = None
    if doc.contact_id:
        contact = (await session.execute(select(Contact).where(Contact.id == doc.contact_id))).scalar_one_or_none()
    cur = doc.currency.value
    number = doc.quote_number if kind == "quote" else doc.invoice_number
    title = "Cotización" if kind == "quote" else "Factura (documento comercial interno)"
    meta = ""
    if kind == "quote" and doc.valid_until:
        meta = f"Válida hasta: {doc.valid_until:%d/%m/%Y}"
    if kind == "invoice":
        meta = f"Emisión: {doc.issue_date or '—'} · Vence: {doc.due_date or '—'}"
    rows = "".join(
        f"<tr><td>{escape(i.description)}</td><td class='r'>{i.quantity}</td>"
        f"<td class='r'>{_money(i.unit_price, cur)}</td><td class='r'>{_money(i.discount, cur)}</td>"
        f"<td class='r'>{_money(i.tax, cur)}</td><td class='r'>{_money(i.total, cur)}</td></tr>"
        for i in doc.items
    )
    who = escape(company.name) + (f"<br>{escape(contact.first_name)} {escape(contact.last_name or '')}" if contact else "")
    note = (f"<p>{escape(doc.notes)}</p>" if doc.notes else "")
    legal = "<p class='note'>Documento comercial interno; no constituye comprobante electrónico válido ante Hacienda.</p>" if kind == "invoice" else ""
    html = f"""<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
    <h1>BeMarketing</h1><p class="muted">{title} {escape(number)}<br>{meta}</p>
    <p><b>Cliente</b><br>{who}</p>
    <table><tr><th>Descripción</th><th class="r">Cant.</th><th class="r">Precio</th><th class="r">Desc.</th><th class="r">IVA</th><th class="r">Total</th></tr>{rows}</table>
    <table class="totals" style="width:50%;margin-left:50%">
      <tr><td>Subtotal</td><td class="r">{_money(doc.subtotal, cur)}</td></tr>
      <tr><td>Descuento</td><td class="r">-{_money(doc.discount, cur)}</td></tr>
      <tr><td>IVA</td><td class="r">{_money(doc.tax, cur)}</td></tr>
      <tr class="total"><td>Total</td><td class="r">{_money(doc.total, cur)}</td></tr></table>
    {note}{legal}</body></html>"""
    return HTML(string=html).write_pdf()
