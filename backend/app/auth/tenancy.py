"""Per-tenant data isolation (T1) request-side helpers.

Tenancy model:
  * Platform owner is tenantless -- get_tenant() returns None, which bypasses
    EVERY filter (sees and writes across tenants). The seeded owner has no
    ww_tenant attribute/claim.
  * admin/viewer are scoped to their own ww_tenant.

Scoping is ADDITIVE to the existing require_* role guards -- it never replaces
them.
"""
import uuid

from fastapi import Depends

from app.auth.kc_admin import TENANT_ATTR, tenant_of
from app.auth.middleware import get_current_user

# Bootstrap customer, created by migration 009 and re-asserted by 012. Rows
# created by a tenantless owner, and all pre-T1 rows, belong here.
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _tenant_claim(user: dict) -> str | None:
    """The caller's tenant id, or None for the tenantless platform owner.

    tenant_of() reads the Keycloak admin-API shape (attributes map). A plain
    bearer/cookie JWT instead carries ww_tenant as a top-level claim, so fall
    back to that -- otherwise every real admin token would read as tenantless
    and isolation would silently never engage.
    """
    return tenant_of(user) or user.get(TENANT_ATTR)


async def get_tenant(user: dict = Depends(get_current_user)) -> str | None:
    """FastAPI dependency: caller's tenant id, or None for the owner."""
    return _tenant_claim(user)


def scope(stmt, model, tenant: str | None):
    """Add `model.tenant_id == tenant` to a statement, unless tenant is None.

    A None tenant is the platform owner: the statement is returned unchanged
    so the owner reads across every tenant.
    """
    if tenant is None:
        return stmt
    return stmt.where(model.tenant_id == uuid.UUID(tenant))


def tenant_for_write(tenant: str | None) -> uuid.UUID:
    """Tenant id to stamp on an insert: the caller's, or the bootstrap tenant
    when the caller is the tenantless owner."""
    return uuid.UUID(tenant) if tenant is not None else DEFAULT_TENANT_ID
