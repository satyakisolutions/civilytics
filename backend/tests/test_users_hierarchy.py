# backend/tests/test_users_hierarchy.py
"""Privilege-escalation guards on hierarchical onboarding.

The one property worth testing here: nobody can create a peer or a superior.
Everything else in routes/users.py is Keycloak plumbing.
"""
import pytest
from fastapi import HTTPException

from app.api.routes import users as mod
from app.api.routes.users import (
    CreateUserRequest,
    SetEnabledRequest,
    UpdateUserRequest,
    _normalise_phone,
)

# An owner has no tenant -- they see every customer.
OWNER = {"sub": "owner-sub", "preferred_username": "owner",
         "realm_access": {"roles": ["owner", "admin", "viewer"]}}
# One admin == one customer, so an admin always carries a tenant claim.
ADMIN = {"sub": "admin-sub", "preferred_username": "adm", "ww_tenant": "tenant-a",
         "realm_access": {"roles": ["admin", "viewer"]}}
OTHER_ADMIN = {"sub": "other-admin", "preferred_username": "adm2",
               "ww_tenant": "tenant-b",
               "realm_access": {"roles": ["admin", "viewer"]}}
VIEWER = {"sub": "viewer-sub", "preferred_username": "vw", "ww_tenant": "tenant-a",
          "realm_access": {"roles": ["viewer"]}}


def _req(role: str, **over) -> CreateUserRequest:
    kwargs = dict(
        username="newuser", email="new@test.local",
        full_name="New User", phone="9876543210", zone="Adugodi", role=role,
        customer="Adugodi Ward Office",
    )
    kwargs.update(over)
    return CreateUserRequest(**kwargs)


@pytest.fixture
def fake_kc(monkeypatch):
    """Records calls so a blocked request can be proven to reach nothing."""
    state = {"created": [], "roles_assigned": [], "emails": [],
             "enabled": [], "deleted": [], "updated": [],
             "users": {}, "direct_roles": {}}

    async def create_user(**kw):
        state["created"].append(kw)
        uid = f"id-{kw['username']}"
        attrs = {mod.kc_admin.INVITED_BY_ATTR: [kw["invited_by"]],
                 mod.kc_admin.TENANT_ATTR: [kw["tenant"]]}
        if kw.get("customer"):
            attrs[mod.kc_admin.CUSTOMER_ATTR] = [kw["customer"]]
        if kw.get("phone"):
            attrs[mod.kc_admin.PHONE_ATTR] = [kw["phone"]]
        if kw.get("zone"):
            attrs[mod.kc_admin.ZONE_ATTR] = [kw["zone"]]
        state["users"][uid] = {
            "id": uid, "username": kw["username"], "email": kw["email"],
            "firstName": kw.get("first_name", ""), "lastName": kw.get("last_name", ""),
            "enabled": True, "emailVerified": False, "attributes": attrs,
        }
        return uid

    async def update_profile(uid, **kw):
        state["updated"].append((uid, kw))
        user = state["users"][uid]
        if kw.get("first_name") is not None:
            user["firstName"] = kw["first_name"]
        if kw.get("last_name") is not None:
            user["lastName"] = kw["last_name"]
        if kw.get("email") is not None:
            user["email"] = kw["email"]
        for key, attr in (("phone", mod.kc_admin.PHONE_ATTR), ("zone", mod.kc_admin.ZONE_ATTR)):
            if kw.get(key) is not None:
                user["attributes"][attr] = [kw[key]] if kw[key] else []

    async def assign_realm_role(uid, role):
        state["roles_assigned"].append((uid, role))
        state["direct_roles"][uid] = [role]

    async def send_setup_email(uid):
        state["emails"].append(uid)

    async def get_user(uid):
        if uid not in state["users"]:
            raise mod.KeycloakAdminError("User not found", status_code=404)
        return state["users"][uid]

    async def realm_roles_of(uid):
        return state["direct_roles"].get(uid, [])

    async def set_enabled(uid, enabled):
        state["enabled"].append((uid, enabled))

    async def delete_user(uid):
        state["deleted"].append(uid)

    for name, fn in [
        ("create_user", create_user), ("assign_realm_role", assign_realm_role),
        ("send_setup_email", send_setup_email), ("get_user", get_user),
        ("realm_roles_of", realm_roles_of), ("set_enabled", set_enabled),
        ("update_profile", update_profile),
        ("delete_user", delete_user),
    ]:
        monkeypatch.setattr(mod.kc_admin, name, fn)
    return state


# ── the escalation guard ───────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "owner"])
async def test_admin_cannot_create_peer_or_superior(fake_kc, role):
    with pytest.raises(HTTPException) as exc:
        await mod.create_managed_user(_req(role), actor=ADMIN)
    assert exc.value.status_code == 403
    # Nothing was created: the guard runs before any Keycloak call.
    assert fake_kc["created"] == []
    assert fake_kc["roles_assigned"] == []


async def test_owner_cannot_create_another_owner(fake_kc):
    """`owner` appears in no row of _GRANTABLE, so even an owner is refused.

    A second owner is a realm-level decision, made in the Keycloak console
    with an audit trail, not something the app hands out.
    """
    with pytest.raises(HTTPException) as exc:
        await mod.create_managed_user(_req("owner"), actor=OWNER)
    assert exc.value.status_code == 403
    assert fake_kc["created"] == []


async def test_owner_creates_admin_opening_a_new_tenant(fake_kc):
    out = await mod.create_managed_user(_req("admin"), actor=OWNER)
    assert out.role == "admin"
    assert fake_kc["roles_assigned"] == [("id-newuser", "admin")]
    assert fake_kc["emails"] == ["id-newuser"]
    assert fake_kc["created"][0]["invited_by"] == "owner-sub"
    # A new admin is a new customer, so a fresh tenant id is minted.
    assert fake_kc["created"][0]["tenant"]
    assert fake_kc["created"][0]["tenant"] != "tenant-a"
    assert out.customer == "Adugodi Ward Office"


async def test_admin_requires_a_customer_name(fake_kc):
    with pytest.raises(HTTPException) as exc:
        await mod.create_managed_user(_req("admin", customer="  "), actor=OWNER)
    assert exc.value.status_code == 422
    assert fake_kc["created"] == []


async def test_viewers_inherit_their_admins_tenant(fake_kc):
    """One admin, many viewers -- all in the same tenant."""
    for n in range(3):
        await mod.create_managed_user(_req("viewer", username=f"viewer{n}"), actor=ADMIN)
    assert [c["tenant"] for c in fake_kc["created"]] == ["tenant-a"] * 3


async def test_viewer_tenant_is_never_taken_from_the_request(fake_kc):
    """An admin cannot inject a viewer into another customer's tenant."""
    req = _req("viewer", customer="Some Other Customer")
    await mod.create_managed_user(req, actor=ADMIN)
    assert fake_kc["created"][0]["tenant"] == "tenant-a"
    assert fake_kc["created"][0]["customer"] == ""


async def test_admin_creates_viewer(fake_kc):
    out = await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    assert out.role == "viewer"
    assert fake_kc["created"][0]["invited_by"] == "admin-sub"
    assert fake_kc["created"][0]["tenant"] == "tenant-a"


async def test_no_password_is_ever_set(fake_kc):
    """Keycloak emails the set-password link; this service never sees one."""
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    assert "password" not in fake_kc["created"][0]
    assert "credentials" not in fake_kc["created"][0]


async def test_unknown_role_rejected(fake_kc):
    with pytest.raises(HTTPException) as exc:
        await mod.create_managed_user(_req("realm-admin"), actor=OWNER)
    assert exc.value.status_code == 403


async def test_grantable_roles(fake_kc):
    assert (await mod.grantable_roles(actor=OWNER))["roles"] == ["admin", "viewer"]
    assert (await mod.grantable_roles(actor=ADMIN))["roles"] == ["viewer"]
    assert (await mod.grantable_roles(actor=VIEWER))["roles"] == []


# ── ownership scoping ──────────────────────────────────────────────────────

async def test_admin_cannot_manage_another_customers_viewer(fake_kc):
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)   # tenant-a
    with pytest.raises(HTTPException) as exc:
        await mod.delete_managed_user("id-newuser", actor=OTHER_ADMIN)  # tenant-b
    # 404, not 403: an admin cannot even probe for users outside their scope.
    assert exc.value.status_code == 404
    assert fake_kc["deleted"] == []


async def test_admin_manages_own_viewer(fake_kc):
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    await mod.set_user_enabled("id-newuser", SetEnabledRequest(enabled=False), actor=ADMIN)
    assert fake_kc["enabled"] == [("id-newuser", False)]
    await mod.delete_managed_user("id-newuser", actor=ADMIN)
    assert fake_kc["deleted"] == ["id-newuser"]


async def test_admin_cannot_manage_an_admin_in_its_own_tenant(fake_kc):
    """A customer with two admins must not let either disable the other."""
    await mod.create_managed_user(_req("admin"), actor=OWNER)
    fake_kc["users"]["id-newuser"]["attributes"][mod.kc_admin.TENANT_ATTR] = ["tenant-a"]
    with pytest.raises(HTTPException) as exc:
        await mod.delete_managed_user("id-newuser", actor=ADMIN)
    assert exc.value.status_code == 403
    assert fake_kc["deleted"] == []


async def test_owner_manages_every_tenant(fake_kc):
    await mod.create_managed_user(_req("admin"), actor=OWNER)
    await mod.delete_managed_user("id-newuser", actor=OWNER)
    assert fake_kc["deleted"] == ["id-newuser"]


@pytest.mark.parametrize("actor", [OWNER, ADMIN])
async def test_nobody_manages_their_own_account(fake_kc, actor):
    """Stops the last owner locking the realm out of its own app."""
    with pytest.raises(HTTPException) as exc:
        await mod.delete_managed_user(actor["sub"], actor=actor)
    assert exc.value.status_code == 403


async def test_account_survives_a_failed_setup_email(fake_kc, monkeypatch):
    """SMTP down must not make a created user look like a failed request."""
    async def boom(uid):
        raise mod.KeycloakAdminError("mail failed", status_code=502)
    monkeypatch.setattr(mod.kc_admin, "send_setup_email", boom)
    out = await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    assert out.username == "newuser"
    assert fake_kc["roles_assigned"] == [("id-newuser", "viewer")]


# ── field handling ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "+919876543210"),
    ("+91 98765 43210", "+919876543210"),
    ("+91-9876543210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("(98765) 43210", "+919876543210"),
    ("", ""),
    ("   ", ""),
])
def test_phone_normalised_to_one_shape(raw, expected):
    assert _normalise_phone(raw) == expected


@pytest.mark.parametrize("bad", [
    "1234567890",       # starts with 1 -- not an Indian mobile
    "5876543210",       # starts with 5
    "98765432",         # too short
    "98765432101",      # too long
    "+1 415 555 0123",  # wrong country
    "abcdefghij",
])
def test_bad_phone_rejected(bad):
    with pytest.raises(HTTPException) as exc:
        _normalise_phone(bad)
    assert exc.value.status_code == 422


async def test_phone_optional_for_every_role(fake_kc):
    """Phone is optional: a viewer or admin with no number still onboards."""
    for role, actor in [("admin", OWNER), ("viewer", ADMIN)]:
        fake_kc["created"].clear()
        req = _req(role, phone="", username=f"u-{role}")
        out = await mod.create_managed_user(req, actor=actor)
        assert fake_kc["created"][0]["phone"] == ""
        assert out.phone is None


async def test_full_name_split_for_keycloak(fake_kc):
    await mod.create_managed_user(_req("viewer", full_name="Lakshmi Devi Rao"), actor=ADMIN)
    created = fake_kc["created"][0]
    assert created["first_name"] == "Lakshmi"
    assert created["last_name"] == "Devi Rao"


async def test_single_token_name_leaves_surname_blank(fake_kc):
    await mod.create_managed_user(_req("viewer", full_name="Ravi"), actor=ADMIN)
    assert fake_kc["created"][0] == {**fake_kc["created"][0], "first_name": "Ravi", "last_name": ""}


async def test_zone_and_phone_round_trip(fake_kc):
    out = await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    assert out.zone == "Adugodi"
    assert out.phone == "+919876543210"


async def test_edit_updates_only_sent_fields(fake_kc):
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    out = await mod.update_managed_user(
        "id-newuser", UpdateUserRequest(zone="Lakkasandra"), actor=ADMIN
    )
    assert out.zone == "Lakkasandra"
    # Phone untouched because it was not sent.
    assert out.phone == "+919876543210"
    assert fake_kc["updated"][0][1]["phone"] is None


async def test_edit_normalises_phone(fake_kc):
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    out = await mod.update_managed_user(
        "id-newuser", UpdateUserRequest(phone="+91 90000 00001"), actor=ADMIN
    )
    assert out.phone == "+919000000001"


async def test_edit_cannot_change_role(fake_kc):
    """The escalation hole from the other side: create a viewer, patch to admin."""
    assert not hasattr(UpdateUserRequest, "role")
    assert "role" not in UpdateUserRequest.model_fields


async def test_admin_cannot_edit_another_customers_user(fake_kc):
    await mod.create_managed_user(_req("viewer"), actor=ADMIN)
    with pytest.raises(HTTPException) as exc:
        await mod.update_managed_user(
            "id-newuser", UpdateUserRequest(zone="X"), actor=OTHER_ADMIN
        )
    assert exc.value.status_code == 404
    assert fake_kc["updated"] == []
