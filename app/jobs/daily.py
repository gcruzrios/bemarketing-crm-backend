"""Tarea diaria (cron / Heroku Scheduler):  python -m app.jobs.daily"""
import asyncio
from datetime import timedelta

from sqlalchemy import select, update

from app.core.database import SessionLocal, engine
from app.core.security import utcnow
from app.models import Activity, ActivityStatus, Invoice, InvoiceStatus, Notification, Quote, QuoteStatus
from app.services.notification_service import notify, notify_managers


async def run():
    today = utcnow().date()
    async with SessionLocal() as s:
        # Facturas vencidas
        inv = (await s.execute(select(Invoice).where(
            Invoice.deleted_at.is_(None), Invoice.due_date < today,
            Invoice.status.in_([InvoiceStatus.issued, InvoiceStatus.sent, InvoiceStatus.partial])))).scalars().all()
        for i in inv:
            i.status = InvoiceStatus.overdue
            await notify_managers(s, "invoice_overdue", f"Factura {i.invoice_number} vencida", None, "invoice", i.id)
            if i.created_by:
                await notify(s, i.created_by, "invoice_overdue", f"Factura {i.invoice_number} vencida", None, "invoice", i.id)
        # Cotizaciones expiradas
        res = await s.execute(update(Quote).where(
            Quote.deleted_at.is_(None), Quote.valid_until < today, Quote.status.in_([QuoteStatus.sent, QuoteStatus.viewed]))
            .values(status=QuoteStatus.expired))
        # Actividades que vencen en las próximas 24h (una notificación por actividad)
        soon = (await s.execute(select(Activity).where(
            Activity.deleted_at.is_(None), Activity.status == ActivityStatus.pending, Activity.assigned_to.is_not(None),
            Activity.due_date.is_not(None), Activity.due_date <= utcnow() + timedelta(hours=24)))).scalars().all()
        sent = 0
        for a in soon:
            exists = (await s.execute(select(Notification.id).where(
                Notification.entity_id == a.id, Notification.type == "activity_due"))).first()
            if not exists:
                await notify(s, a.assigned_to, "activity_due", f"Actividad por vencer: {a.subject}", None, "activity", a.id)
                sent += 1
        await s.commit()
    print(f"overdue_invoices={len(inv)} expired_quotes={res.rowcount} activity_notifications={sent}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
