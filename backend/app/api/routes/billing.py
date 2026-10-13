import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.auth.middleware import require_admin
from app.db.billing_models import (
    BillingContact,
    Invoice,
    ServiceRequest,
    Subscription,
)
from app.db.models import Camera, CameraStatus, ViolationEvent
from app.schemas.billing import (
    BillingContactResponse,
    BillingContactUpdate,
    InvoiceResponse,
    ServiceRequestCreate,
    ServiceRequestResponse,
    SubscriptionResponse,
    UsageResponse,
)

router = APIRouter(prefix="/billing", tags=["billing"])

_DEFAULT_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _paise_to_rupees(paise: int) -> float:
    return round(paise / 100, 2)


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(
        select(Subscription).where(Subscription.tenant_id == _DEFAULT_TENANT).limit(1)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "No subscription found")
    return SubscriptionResponse(
        id=sub.id,
        plan_name=sub.plan_name,
        description=sub.description,
        status=sub.status.value,
        billing_cycle=sub.billing_cycle.value,
        start_date=sub.start_date,
        next_renewal=sub.next_renewal,
        monthly_amount_rupees=_paise_to_rupees(sub.monthly_amount_paise),
        subscription_ext_id=sub.subscription_ext_id,
        created_at=sub.created_at,
    )


@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == _DEFAULT_TENANT)
        .order_by(Invoice.issued_date.desc())
    )
    if status:
        stmt = stmt.where(Invoice.payment_status == status)
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [
        InvoiceResponse(
            id=inv.id,
            invoice_number=inv.invoice_number,
            billing_period_start=inv.billing_period_start,
            billing_period_end=inv.billing_period_end,
            issued_date=inv.issued_date,
            due_date=inv.due_date,
            amount_rupees=_paise_to_rupees(inv.amount_paise),
            tax_rupees=_paise_to_rupees(inv.tax_paise),
            total_rupees=_paise_to_rupees(inv.total_paise),
            payment_status=inv.payment_status.value,
            razorpay_payment_id=inv.razorpay_payment_id,
            pdf_url=inv.pdf_url,
            created_at=inv.created_at,
        )
        for inv in result.scalars().all()
    ]


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: uuid.UUID,
    _user: dict = Depends(require_admin),
):
    # TODO: generate or serve actual invoice PDF
    raise HTTPException(501, "Invoice PDF generation not implemented yet")


@router.get("/contact", response_model=BillingContactResponse)
async def get_billing_contact(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(
        select(BillingContact).where(BillingContact.tenant_id == _DEFAULT_TENANT)
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "No billing contact found")
    return contact


@router.put("/contact", response_model=BillingContactResponse)
async def update_billing_contact(
    body: BillingContactUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(
        select(BillingContact).where(BillingContact.tenant_id == _DEFAULT_TENANT)
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "No billing contact found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.post("/requests", response_model=ServiceRequestResponse, status_code=201)
async def create_service_request(
    body: ServiceRequestCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    req = ServiceRequest(
        tenant_id=_DEFAULT_TENANT,
        request_type=body.request_type,
        workspace=body.workspace,
        location=body.location,
        expected_date=body.expected_date,
        notes=body.notes,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    today = date.today()
    period_start = today.replace(day=1)
    if today.month == 12:
        period_end = date(today.year + 1, 1, 1)
    else:
        period_end = today.replace(month=today.month + 1, day=1)

    cams_result = await db.execute(
        select(Camera).where(
            Camera.tenant_id == _DEFAULT_TENANT,
            Camera.deleted_at.is_(None),
        )
    )
    cameras = cams_result.scalars().all()
    active = [c for c in cameras if c.status == CameraStatus.online]

    events_result = await db.execute(
        select(func.count(ViolationEvent.id)).where(
            ViolationEvent.camera_id.in_([c.id for c in cameras]),
            ViolationEvent.timestamp >= datetime.combine(period_start, datetime.min.time()),
            ViolationEvent.timestamp < datetime.combine(period_end, datetime.min.time()),
        )
    )
    events_count = events_result.scalar() or 0

    return UsageResponse(
        active_cameras=len(active),
        total_cameras=len(cameras),
        cameras=[
            {
                "id": str(c.id),
                "name": c.name,
                "location": c.location or "",
                "status": c.status.value,
            }
            for c in cameras
        ],
        events_this_period=events_count,
        storage_gb=round(events_count * 0.037, 1),  # ~37MB per event estimate
        billing_period_start=period_start,
        billing_period_end=period_end,
    )


# --- Razorpay payment stubs ---

@router.post("/payments/razorpay/create-order")
async def razorpay_create_order(_user: dict = Depends(require_admin)):
    # TODO: integrate with Razorpay Orders API
    raise HTTPException(501, "Payment integration pending — contact Satyaki Solutions")


@router.post("/payments/razorpay/verify")
async def razorpay_verify_payment(_user: dict = Depends(require_admin)):
    # TODO: verify Razorpay payment signature
    raise HTTPException(501, "Payment integration pending — contact Satyaki Solutions")


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook():
    # TODO: handle Razorpay webhook events (payment.captured, etc.)
    return {"status": "ok"}
