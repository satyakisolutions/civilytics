from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class SubscriptionResponse(BaseModel):
    id: UUID
    plan_name: str
    description: str | None
    status: str
    billing_cycle: str
    start_date: date
    next_renewal: date
    monthly_amount_rupees: float
    subscription_ext_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceResponse(BaseModel):
    id: UUID
    invoice_number: str
    billing_period_start: date
    billing_period_end: date
    issued_date: date
    due_date: date
    amount_rupees: float
    tax_rupees: float
    total_rupees: float
    payment_status: str
    razorpay_payment_id: str | None
    pdf_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class BillingContactResponse(BaseModel):
    id: UUID
    org_name: str
    contact_name: str
    email: str
    phone: str | None
    address: str | None

    class Config:
        from_attributes = True


class BillingContactUpdate(BaseModel):
    contact_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None


class ServiceRequestCreate(BaseModel):
    request_type: str = Field(..., pattern=r"^(add_camera|change_plan|cancel)$")
    workspace: str | None = None
    location: str | None = None
    expected_date: date | None = None
    notes: str | None = None


class ServiceRequestResponse(BaseModel):
    id: UUID
    request_type: str
    workspace: str | None
    location: str | None
    expected_date: date | None
    notes: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class UsageResponse(BaseModel):
    active_cameras: int
    total_cameras: int
    cameras: list[dict]
    events_this_period: int
    storage_gb: float
    billing_period_start: date
    billing_period_end: date
