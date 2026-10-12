# backend/app/auth/kc_admin.py
"""Keycloak Admin REST client, used only for user onboarding.

Creating a user is not something the Direct Access Grant can do, so in-app
onboarding has to go through Keycloak's Admin REST API. That API is reached
with a SERVICE ACCOUNT token (client_credentials on the same confidential
client as the login flow), never with the calling user's own token.

SECURITY NOTE -- read before changing anything here.

The service account's own `realm-management` roles bound what this module is
*able* to do; they do NOT bound what any particular caller may ask it to do.
The service account can create an `admin` just as easily as a `viewer`. The
only thing stopping an `admin` from minting another `admin` is the role check
in api/routes/users.py (`grantable_roles`). This module is deliberately dumb:
it performs what it is told. Keep the authorisation decision in the route.

Keycloak setup required (Clients -> wastewatch-api -> Service accounts roles):
  realm-management: manage-users, view-users, query-users
Plus "Service accounts roles" enabled on the client. Without those the calls
here return 403 and onboarding fails closed.
"""
import logging
import time

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Custom user attribute recording who onboarded this user. An admin may only
# manage users carrying their own `sub` here. Stored as an attribute rather
# than a local table so there is exactly one source of truth for identity --
# a local mirror would drift the moment someone touches the Keycloak console.
INVITED_BY_ATTR = "ww_invited_by"

# Profile attributes. Keycloak has no native phone or zone field, so these
# ride along as custom user attributes. Keycloak stores every attribute as a
# list of strings, hence the [value] wrapping throughout.
PHONE_ATTR = "ww_phone"
ZONE_ATTR = "ww_zone"

# Tenant id. One admin == one customer (one municipal body), so an admin IS
# a tenant boundary and their viewers belong to that customer, not to that
# person. Scoping on INVITED_BY_ATTR alone would orphan every viewer the
# moment their onboarding admin is replaced -- the replacement admin is a
# different `sub` and would see none of them. The tenant id is minted once,
# when an owner creates the admin, and inherited unchanged by every viewer
# that admin onboards, so it survives any individual account being deleted.
TENANT_ATTR = "ww_tenant"

# Human-readable customer name, carried alongside the id so the owner's user
# list is readable without a second lookup.
CUSTOMER_ATTR = "ww_customer"

# Actions Keycloak asks the new user to complete via the setup email. The user
# is created with NO credential at all, so the password is never known to the
# onboarding admin, and never transits this process.
SETUP_ACTIONS = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]

_token: str | None = None
_token_expires_at: float = 0.0


class KeycloakAdminError(RuntimeError):
    """Admin API call failed. Carries the upstream status for the route."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _admin_base() -> str:
    return f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}"


async def _service_token() -> str:
    """client_credentials token for the service account, cached until expiry."""
    global _token, _token_expires_at
    now = time.monotonic()
    if _token and now < _token_expires_at:
        return _token

    if not settings.keycloak_client_secret:
        # A public client has no service account, so onboarding cannot work.
        raise KeycloakAdminError(
            "User management requires a confidential Keycloak client "
            "(WW_KEYCLOAK_CLIENT_SECRET is unset)",
            status_code=503,
        )

    url = (
        f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
        f"/protocol/openid-connect/token"
    )
    form = {
        "grant_type": "client_credentials",
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data=form)
    except httpx.RequestError as exc:
        raise KeycloakAdminError("Keycloak unreachable", status_code=503) from exc

    if resp.status_code != 200:
        logger.error("Service account token request failed: HTTP %d", resp.status_code)
        raise KeycloakAdminError("Service account authentication failed", status_code=503)

    payload = resp.json()
    _token = payload["access_token"]
    # Renew 30s early so a token never expires mid-request.
    _token_expires_at = now + max(payload.get("expires_in", 60) - 30, 10)
    return _token


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    token = await _service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(
                method, f"{_admin_base()}{path}", headers=headers, **kwargs
            )
    except httpx.RequestError as exc:
        raise KeycloakAdminError("Keycloak unreachable", status_code=503) from exc

    if resp.status_code == 403:
        # Almost always missing realm-management roles on the service account.
        logger.error("Keycloak admin call forbidden: %s %s", method, path)
        raise KeycloakAdminError(
            "Service account lacks realm-management permissions", status_code=503
        )
    if resp.status_code == 409:
        raise KeycloakAdminError("Username or email already exists", status_code=409)
    if resp.status_code == 404:
        raise KeycloakAdminError("User not found", status_code=404)
    if resp.status_code >= 400:
        # Body is not logged: Keycloak echoes submitted field values.
        logger.error("Keycloak admin call failed: %s %s -> %d", method, path, resp.status_code)
        raise KeycloakAdminError("Keycloak rejected the request", status_code=502)
    return resp


# ── realm roles ────────────────────────────────────────────────────────────

async def _realm_role(name: str) -> dict:
    resp = await _request("GET", f"/roles/{name}")
    return resp.json()


async def assign_realm_role(user_id: str, role: str) -> None:
    rep = await _realm_role(role)
    await _request(
        "POST",
        f"/users/{user_id}/role-mappings/realm",
        json=[{"id": rep["id"], "name": rep["name"]}],
    )


async def realm_roles_of(user_id: str) -> list[str]:
    """Directly assigned realm roles. Composites are NOT expanded.

    Deliberate: onboarding assigns exactly one role, and the hierarchy check
    must see what was granted, not what it implies. `owner` is a composite
    including `admin`, so an expanded view would make every owner look like
    an admin and an admin's ownership check would start matching owners.
    """
    resp = await _request("GET", f"/users/{user_id}/role-mappings/realm")
    return [r["name"] for r in resp.json()]


# ── users ──────────────────────────────────────────────────────────────────

async def create_user(
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    invited_by: str,
    tenant: str,
    customer: str = "",
    phone: str = "",
    zone: str = "",
) -> str:
    """Create a disabled-credential user. Returns the new user's id."""
    attributes: dict[str, list[str]] = {
        INVITED_BY_ATTR: [invited_by],
        TENANT_ATTR: [tenant],
    }
    if customer:
        attributes[CUSTOMER_ATTR] = [customer]
    if phone:
        attributes[PHONE_ATTR] = [phone]
    if zone:
        attributes[ZONE_ATTR] = [zone]
    body = {
        "username": username,
        "email": email,
        "firstName": first_name,
        "lastName": last_name,
        "enabled": True,
        # The setup email carries VERIFY_EMAIL, so leave this false and let
        # Keycloak flip it when the user follows the link.
        "emailVerified": False,
        "attributes": attributes,
        # No "credentials" key at all -- there is no password to leak.
        "requiredActions": list(SETUP_ACTIONS),
    }
    resp = await _request("POST", "/users", json=body)
    location = resp.headers.get("Location", "")
    user_id = location.rsplit("/", 1)[-1]
    if not user_id:
        raise KeycloakAdminError("Keycloak did not return the new user id")
    return user_id


async def send_setup_email(user_id: str) -> None:
    """Ask Keycloak to email a set-password link.

    Requires realm SMTP to be configured. If it is not, Keycloak returns 500
    and the caller surfaces that -- the user exists but cannot sign in yet,
    which is recoverable by resending once SMTP works.
    """
    await _request(
        "PUT",
        f"/users/{user_id}/execute-actions-email",
        json=list(SETUP_ACTIONS),
        params={"client_id": settings.keycloak_client_id},
    )


async def get_user(user_id: str) -> dict:
    resp = await _request("GET", f"/users/{user_id}")
    return resp.json()


async def list_users(*, tenant: str | None = None, limit: int = 200) -> list[dict]:
    """List realm users, optionally only those belonging to one tenant."""
    params: dict = {"max": limit, "briefRepresentation": "false"}
    if tenant is not None:
        # Keycloak's attribute query syntax. Exact match on the stored value.
        params["q"] = f"{TENANT_ATTR}:{tenant}"
    resp = await _request("GET", "/users", params=params)
    return resp.json()


async def update_profile(
    user_id: str,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    zone: str | None = None,
) -> None:
    """Patch profile fields, leaving everything else alone.

    Keycloak's user PUT REPLACES the whole attributes map, so the current
    attributes are read first and merged. Writing only the changed key would
    silently drop ww_invited_by and orphan the user from its onboarding admin
    -- an admin would lose the ability to manage a user they created.
    """
    current = await get_user(user_id)
    attributes = dict(current.get("attributes") or {})
    if phone is not None:
        attributes[PHONE_ATTR] = [phone] if phone else []
    if zone is not None:
        attributes[ZONE_ATTR] = [zone] if zone else []

    body: dict = {"attributes": attributes}
    if first_name is not None:
        body["firstName"] = first_name
    if last_name is not None:
        body["lastName"] = last_name
    if email is not None and email != current.get("email"):
        body["email"] = email
        # A changed address is unverified until the user proves it, otherwise
        # editing the email would be a way to point a verified account at an
        # address nobody controls.
        body["emailVerified"] = False
    await _request("PUT", f"/users/{user_id}", json=body)


async def set_enabled(user_id: str, enabled: bool) -> None:
    await _request("PUT", f"/users/{user_id}", json={"enabled": enabled})


async def set_password(user_id: str, password: str, *, temporary: bool = True) -> None:
    """Set a credential directly. Bootstrap-only -- normal onboarding (create_user)
    never does this; it leaves the user with no credential and emails a setup link
    instead. This exists solely so scripts/seed_owner.py can mint the very first
    owner, who by definition has no admin yet to invite them.
    """
    await _request(
        "PUT",
        f"/users/{user_id}/reset-password",
        json={"type": "password", "value": password, "temporary": temporary},
    )


async def users_with_role(role: str) -> list[dict]:
    resp = await _request("GET", f"/roles/{role}/users")
    return resp.json()


async def clear_required_actions(user_id: str) -> None:
    """Used only by scripts/seed_owner.py. create_user() sets requiredActions
    (UPDATE_PASSWORD, VERIFY_EMAIL) for the normal email-invite onboarding
    flow, but the bootstrap owner has no redirect-flow login to satisfy
    them -- auth.py's login route is Direct Access Grant only, which
    Keycloak refuses outright ("Account is not fully set up") for any user
    with pending required actions or a temporary credential. Bootstrap
    must clear this, and must set a non-temporary password, or it creates
    an owner that can never sign in.
    """
    await _request("PUT", f"/users/{user_id}", json={"requiredActions": []})


async def delete_user(user_id: str) -> None:
    await _request("DELETE", f"/users/{user_id}")


def _attr(user: dict, key: str) -> str | None:
    values = (user.get("attributes") or {}).get(key) or []
    return values[0] if values else None


def invited_by_of(user: dict) -> str | None:
    return _attr(user, INVITED_BY_ATTR)


def phone_of(user: dict) -> str | None:
    return _attr(user, PHONE_ATTR)


def zone_of(user: dict) -> str | None:
    return _attr(user, ZONE_ATTR)


def tenant_of(user: dict) -> str | None:
    return _attr(user, TENANT_ATTR)


def customer_of(user: dict) -> str | None:
    return _attr(user, CUSTOMER_ATTR)
