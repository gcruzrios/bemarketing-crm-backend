from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.deps import require_capability
from app.core.security import utcnow
from app.models import (
    Invoice, InvoiceStatus, Lead, LeadStatus, Opportunity, OpportunityStatus, PipelineStage, Quote,
    QuoteStatus, User, UserRole,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", summary="Métricas según rol",
            description="admin/manager: todo el equipo; sales: cartera propia; accounting: financiero.")
async def dashboard(session: AsyncSession = Depends(get_session), user: User = Depends(require_capability("dashboard:read"))):
    out: dict = {"scope": "team" if user.role in (UserRole.admin, UserRole.manager) else user.role.value}
    own = user.role == UserRole.sales
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if user.role != UserRole.accounting:
        lf = [Lead.deleted_at.is_(None)] + ([Lead.assigned_to == user.id] if own else [])
        rows = (await session.execute(select(Lead.status, func.count()).where(*lf).group_by(Lead.status))).all()
        out["leads_by_status"] = {s.value: n for s, n in rows}
        if not own:
            out["leads_unassigned"] = (await session.execute(select(func.count()).select_from(Lead).where(
                Lead.deleted_at.is_(None), Lead.assigned_to.is_(None), Lead.status.in_([LeadStatus.new, LeadStatus.contacted])))).scalar_one()
        out["leads_new_this_month"] = (await session.execute(select(func.count()).select_from(Lead).where(*lf, Lead.created_at >= month_start))).scalar_one()

        of = [Opportunity.deleted_at.is_(None)] + ([Opportunity.assigned_to == user.id] if own else [])
        rows = (await session.execute(
            select(PipelineStage.name, PipelineStage.stage_order, func.count(Opportunity.id), func.coalesce(func.sum(Opportunity.expected_value), 0))
            .join(Opportunity, Opportunity.stage_id == PipelineStage.id).where(*of, Opportunity.status == OpportunityStatus.open)
            .group_by(PipelineStage.id).order_by(PipelineStage.stage_order))).all()
        out["pipeline"] = [{"stage": n, "count": c, "value": str(v)} for n, _, c, v in rows]
        out["open_opportunities"] = sum(r["count"] for r in out["pipeline"])
        won = (await session.execute(select(func.count(), func.coalesce(func.sum(Opportunity.expected_value), 0)).where(
            *of, Opportunity.status == OpportunityStatus.won, Opportunity.closed_at >= month_start))).one()
        lost = (await session.execute(select(func.count()).where(*of, Opportunity.status == OpportunityStatus.lost, Opportunity.closed_at >= month_start))).scalar_one()
        out["won_this_month"] = {"count": won[0], "value": str(won[1])}
        out["lost_this_month"] = lost
        closed = won[0] + lost
        out["win_rate_this_month"] = round(won[0] / closed, 2) if closed else None

        qf = [Quote.deleted_at.is_(None)] + ([Quote.created_by == user.id] if own else [])
        rows = (await session.execute(select(Quote.status, func.count(), func.coalesce(func.sum(Quote.total), 0)).where(*qf).group_by(Quote.status))).all()
        out["quotes_by_status"] = {s.value: {"count": n, "total": str(t)} for s, n, t in rows}

    if user.role in (UserRole.admin, UserRole.manager, UserRole.accounting):
        rows = (await session.execute(select(Invoice.status, Invoice.currency, func.count(), func.coalesce(func.sum(Invoice.total), 0))
                                      .where(Invoice.deleted_at.is_(None)).group_by(Invoice.status, Invoice.currency))).all()
        out["invoices_by_status"] = [{"status": s.value, "currency": c.value, "count": n, "total": str(t)} for s, c, n, t in rows]
        outstanding = (await session.execute(select(Invoice.currency, func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.deleted_at.is_(None), Invoice.status.in_([InvoiceStatus.issued, InvoiceStatus.sent, InvoiceStatus.partial, InvoiceStatus.overdue]))
            .group_by(Invoice.currency))).all()
        out["receivable"] = {c.value: str(t) for c, t in outstanding}
        paid = (await session.execute(select(Invoice.currency, func.coalesce(func.sum(Invoice.total), 0)).where(
            Invoice.deleted_at.is_(None), Invoice.status == InvoiceStatus.paid, Invoice.paid_date >= month_start.date())
            .group_by(Invoice.currency))).all()
        out["collected_this_month"] = {c.value: str(t) for c, t in paid}
    return out
