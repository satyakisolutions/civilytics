# backend/app/api/routes/users.py
"""Hierarchical user onboarding: owner boards admins, admin boards viewers.

THE RULE THIS FILE EXISTS TO ENFORCE: you may only grant a role strictly
below your own.

    owner  ->  may create admin, viewer
    admin  ->  may create viewer ONLY
    viewer ->  may create nothing

Without that, any admin could create a second admin, or an owner, and
self-promote to global config write access. The UI hides what it cannot grant
(AuthContext's RANK), but the UI is not a control -- every check below runs
server side against the caller's validated token claims.

Tenancy: one admin == one customer. Each admin is created with a freshly
minted `ww_tenant` id, and every viewer that admin onboards inherits it
unchanged. An admin may list and manage only users in their own tenant; an
owner (us) sees every tenant.

`ww_invited_by` is still recorded, but only as provenance -- it is NOT the
access boundary. Scoping on it would orphan every viewer the moment their
onboarding admin is replaced, because the replacement admin has a different
`sub` and would see none of them.

Passwords never pass through this service. Users are created with no
credential at all and Keycloak emails them a set-password link, so an
onboarding admin never learns a subordinate's password.
"""
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import kc_admin
from app.auth.keycloak import extract_roles
from app.auth.middleware import require_admin
from app.auth.kc_admin import KeycloakAdminError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

# Highest-first, so the first match is the caller's effective role. Keycloak
# composites mean an owner's token also carries admin and viewer.
_HIERARCHY = ("owner", "admin", "viewer")

# Who may create whom. Strictly below the actor's own rank -- an admin is
# absent from the admin row on purpose.
_GRANTABLE: dict[str, frozenset[str]] = {
    "owner": frozenset({"admin", "viewer"}),
    "admin": frozenset({"viewer"}),
    "viewer": frozenset(),
}


# Indian mobile numbers: 10 digits starting 6-9, optionally +91/0 prefixed.
# Landlines are excluded on purpose -- these numbers receive SMS/WhatsApp
# alerts, so a number that cannot receive one is a data-entry error.
_PHONE_RE = re.compile(r"^(?:\+?91|0)?([6-9]\d{9})$")


def _normalise_phone(raw: str) -> str:
    """Return +91XXXXXXXXXX, or raise 422. Empty input is allowed.

    Normalising at the boundary means the stored value has exactly one shape.
    Keeping whatever the user typed would make "9876543210" and "+91 98765
    43210" two different people as far as any later lookup is concerned.
    """
    if not raw or not raw.strip():
        return ""
    compact = re.sub(r"[\s\-()]", "", raw.strip())
    match = _PHONE_RE.match(compact)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a 10-digit Indian mobile number starting with 6-9",
        )
    return f"+91{match.group(1)}"


# Claim carrying the caller's own tenant id, published by a protocol mapper
# in the realm. Present on admins; absent on an owner, who has no tenant.
_TENANT_CLAIM = "ww_tenant"


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    # ponytail: a shape check, not full RFC validation. Keycloak is the
    # authority on email validity and rejects a bad address itself, so
    # pulling in email-validator to pre-judge it buys nothing.
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(min_length=2, max_length=128)
    phone: str = Field(default="", max_length=20)
    # Which zone this user is responsible for. Free text matching
    # cameras.zone -- there is no zones table yet, so the API offers the
    # distinct values in use rather than inventing a second vocabulary.
    zone: str = Field(default="", max_length=128)
    role: str
    # Required when an owner creates an admin -- that call opens a new
    # customer. Ignored for a viewer, who inherits their admin's customer.
    customer: str = Field(default="", max_length=160)

    @property
    def names(self) -> tuple[str, str]:
        """Split the single name field Keycloak needs as two.

        One input beats two: people type their full name as one string, and
        "first/last" guesses wrong on most Indian names anyway. Everything
        after the first token is the surname; a single token leaves it blank.
        """
        parts = self.full_name.strip().split()
        return parts[0], " ".join(parts[1:])


class UpdateUserRequest(BaseModel):
    """Every field optional -- only what is sent is changed."""

    full_name: str | None = Field(default=None, min_length=2, max_length=128)
    email: str | None = Field(
        default=None, min_length=3, max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    phone: str | None = Field(default=None, max_length=20)
    zone: str | None = Field(default=None, max_length=128)


class ManagedUser(BaseModel):
    id: str
    username: str
    tenant: str | None = None
    customer: str | None = None
    email: str | None = None
    name: str | None = None
    phone: str | None = None
    zone: str | None = None
    role: str | None = None
    enabled: bool = True
    email_verified: bool = False
    invited_by: str | None = None


class SetEnabledRequest(BaseModel):
    enabled: bool


def _effective_role(claims: dict) -> str:
    roles = set(extract_roles(claims))
    for role in _HIERARCHY:
        if role in roles:
            return role
    # require_admin already ran, so this is unreachable in practice; treat an
    # unexpected claim shape as powerless rather than guessing upward.
    return "viewer"


def _grantable(claims: dict) -> frozenset[str]:
    return _GRANTABLE[_effective_role(claims)]


async def _tenant_of_actor(claims: dict) -> str:
    """The caller's own tenant id.

    Read from the token when the realm's protocol mapper publishes it, which
    costs nothing. Falls back to reading the caller's own user record, so a
    realm that is missing the mapper degrades to one extra call per request
    instead of silently scoping everybody to the empty string -- which would
    merge every customer into one tenant.
    """
    claimed = claims.get(_TENANT_CLAIM)
    if isinstance(claimed, list):
        claimed = claimed[0] if claimed else None
    if claimed:
        return str(claimed)

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=500, detail="Token has no subject claim")
    logger.warning(
        "Token has no %s claim; add the protocol mapper to the realm", _TENANT_CLAIM
    )
    me = await kc_admin.get_user(sub)
    tenant = kc_admin.tenant_of(me)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your account has no customer assigned; ask the owner to fix it",
        )
    return tenant


def _to_managed(user: dict, role: str | None) -> ManagedUser:
    first = user.get("firstName") or ""
    last = user.get("lastName") or ""
    full = f"{first} {last}".strip()
    return ManagedUser(
        id=user["id"],
        username=user.get("username", ""),
        email=user.get("email"),
        name=full or None,
        tenant=kc_admin.tenant_of(user),
        customer=kc_admin.customer_of(user),
        phone=kc_admin.phone_of(user),
        zone=kc_admin.zone_of(user),
        role=role,
        enabled=bool(user.get("enabled", False)),
        email_verified=bool(user.get("emailVerified", False)),
        invited_by=kc_admin.invited_by_of(user),
    )


async def _assert_may_manage(actor: dict, target_id: str) -> dict:
    """Return the target user, or raise if the actor has no business touching it.

    An owner may manage anyone but themselves; blocking self-management stops
    the last owner from disabling or deleting the only account that can
    recover the realm from the app.

    An admin may manage a user only when BOTH hold:
      - the target is in the admin's own tenant, and
      - the target's directly-assigned role is viewer and nothing else.

    The second condition is not redundant: an admin shares a tenant with
    nobody by construction today, but a customer with two admins would
    otherwise let either disable the other.
    """
    actor_sub = actor.get("sub")
    if target_id == actor_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot manage your own account here",
        )

    target = await kc_admin.get_user(target_id)
    if _effective_role(actor) == "owner":
        return target

    if kc_admin.tenant_of(target) != await _tenant_of_actor(actor):
        # 404 rather than 403: an admin should not be able to probe for the
        # existence of users in another customer's tenant.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_roles = set(await kc_admin.realm_roles_of(target_id))
    if not target_roles.issubset({"viewer"}):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can manage this user",
        )
    return target


@router.get("", response_model=list[ManagedUser])
async def list_managed_users(actor: dict = Depends(require_admin)):
    """Owner sees the realm; an admin sees only the users they onboarded."""
    is_owner = _effective_role(actor) == "owner"
    try:
        users = await kc_admin.list_users(
            tenant=None if is_owner else await _tenant_of_actor(actor)
        )
        out = []
        for user in users:
            roles = await kc_admin.realm_roles_of(user["id"])
            # Highest directly-held role; onboarded users have exactly one.
            role = next((r for r in _HIERARCHY if r in roles), None)
            out.append(_to_managed(user, role))
        return out
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/grantable-roles")
async def grantable_roles(actor: dict = Depends(require_admin)):
    """Roles this caller may assign. The UI builds its role picker from this."""
    return {"roles": sorted(_grantable(actor))}


@router.post("", response_model=ManagedUser, status_code=status.HTTP_201_CREATED)
async def create_managed_user(
    body: CreateUserRequest, actor: dict = Depends(require_admin)
):
    allowed = _grantable(actor)
    if body.role not in allowed:
        # The privilege-escalation guard. Covers both "admin asks for admin"
        # and "anyone asks for owner" -- owner is in no row of _GRANTABLE.
        logger.warning(
            "Blocked role escalation: %s (%s) tried to grant '%s'",
            actor.get("preferred_username"),
            _effective_role(actor),
            body.role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You may only create: {', '.join(sorted(allowed)) or 'nobody'}",
        )

    actor_sub = actor.get("sub")
    if not actor_sub:
        raise HTTPException(status_code=500, detail="Token has no subject claim")

    phone = _normalise_phone(body.phone)
    first_name, last_name = body.names

    if body.role == "admin":
        # A new admin is a new customer, so this opens a new tenant.
        customer = body.customer.strip()
        if not customer:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A customer name is required when creating an admin",
            )
        tenant = str(uuid.uuid4())
    else:
        # A viewer inherits their admin's tenant. Never taken from the
        # request body: letting the caller name the tenant would let an
        # admin inject a viewer into another customer's account.
        tenant = await _tenant_of_actor(actor)
        customer = ""

    try:
        user_id = await kc_admin.create_user(
            username=body.username,
            email=body.email,
            first_name=first_name,
            last_name=last_name,
            invited_by=actor_sub,
            tenant=tenant,
            customer=customer,
            phone=phone,
            zone=body.zone.strip(),
        )
        await kc_admin.assign_realm_role(user_id, body.role)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    logger.info(
        "User %s onboarded %s as %s (tenant %s)",
        actor.get("preferred_username"), body.username, body.role, tenant,
    )

    try:
        await kc_admin.send_setup_email(user_id)
        email_sent = True
    except KeycloakAdminError:
        # The account exists and has its role; only the email failed (realm
        # SMTP off or unreachable). Surfacing this as a 201 with a flag beats
        # a 502 that makes a created user look like a failure.
        logger.warning("Setup email failed for %s; account created", body.username)
        email_sent = False

    user = await kc_admin.get_user(user_id)
    result = _to_managed(user, body.role)
    if not email_sent:
        logger.warning("Resend the setup email for %s once SMTP works", body.username)
    return result


@router.patch("/{user_id}", response_model=ManagedUser)
async def update_managed_user(
    user_id: str, body: UpdateUserRequest, actor: dict = Depends(require_admin)
):
    """Edit a managed user's profile. Role is NOT editable here.

    Changing a role is re-granting privilege, and allowing it on this path
    would reopen the escalation hole from the other side: an admin could
    create a viewer and then patch it to admin. Promotion goes through
    delete-and-recreate, or the Keycloak console for an owner.
    """
    await _assert_may_manage(actor, user_id)
    first_name, last_name = (None, None)
    if body.full_name is not None:
        parts = body.full_name.strip().split()
        first_name, last_name = parts[0], " ".join(parts[1:])
    phone = _normalise_phone(body.phone) if body.phone is not None else None
    try:
        await kc_admin.update_profile(
            user_id,
            first_name=first_name,
            last_name=last_name,
            email=body.email,
            phone=phone,
            zone=body.zone.strip() if body.zone is not None else None,
        )
        user = await kc_admin.get_user(user_id)
        roles = await kc_admin.realm_roles_of(user_id)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    logger.info("User %s edited by %s", user_id, actor.get("preferred_username"))
    return _to_managed(user, next((r for r in _HIERARCHY if r in roles), None))


@router.post("/{user_id}/setup-email", status_code=204)
async def resend_setup_email(user_id: str, actor: dict = Depends(require_admin)):
    """Resend the set-password link. Also the password-reset path.

    Direct Access Grant has no self-service reset, so a user who forgets
    their password asks whoever onboarded them to press this.
    """
    await _assert_may_manage(actor, user_id)
    try:
        await kc_admin.send_setup_email(user_id)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    logger.info(
        "Setup email resent to %s by %s", user_id, actor.get("preferred_username")
    )


@router.patch("/{user_id}/enabled", status_code=204)
async def set_user_enabled(
    user_id: str, body: SetEnabledRequest, actor: dict = Depends(require_admin)
):
    """Disable or re-enable a user. Preferred over delete -- it keeps the
    audit trail on everything the account acknowledged or resolved."""
    await _assert_may_manage(actor, user_id)
    try:
        await kc_admin.set_enabled(user_id, body.enabled)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    logger.info(
        "User %s set enabled=%s by %s",
        user_id, body.enabled, actor.get("preferred_username"),
    )


@router.delete("/{user_id}", status_code=204)
async def delete_managed_user(user_id: str, actor: dict = Depends(require_admin)):
    await _assert_may_manage(actor, user_id)
    try:
        await kc_admin.delete_user(user_id)
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    logger.info("User %s deleted by %s", user_id, actor.get("preferred_username"))
