# Plan — hierarchical user onboarding & tenancy

Status: **backend implemented, 39/39 tests pass. Frontend not started.**
Date: 2026-10-06
Branch: `feat/vehicle-dwell-v13-weights`

---

## 1. The model the learner chose

```
owner (Satyaki)  ──onboards──>  admin (= ONE customer)  ──onboards──>  viewer (many)
```

Learner decisions, verbatim intent:

| # | Decision | Chosen |
|---|---|---|
| D1 | Who onboards whom | "super admin boards admin and he boards viewers" |
| D2 | Viewer ownership | Each admin owns their own viewers (not a shared pool) |
| D3 | Password delivery | Keycloak emails a set-password link |
| D4 | Tenancy unit | "One admin means one customer" |
| D5 | Cardinality | "admin has many viewers" (1:N) |
| D6 | Phone | Optional for both admin and viewer |
| D7 | User management location | In-app (resolves DEC-7 toward in-app, hierarchical) |

---

## 2. The one property this exists to enforce

**You may only grant a role strictly below your own.**

```
owner  -> may create admin, viewer
admin  -> may create viewer ONLY
viewer -> may create nothing
```

`owner` appears in **no** row, so not even an owner can mint another owner
from the app — that stays a Keycloak console action with its own audit trail.

Without this an admin creates a second admin, or an owner, and self-promotes
to global config write access.

Enforced server-side against validated token claims. The UI hides what it
cannot grant (`AuthContext.RANK`), but the UI is not a control.

### Two non-obvious guards

1. **Role is not editable via PATCH.** Otherwise the hole reopens from the
   other side: create a viewer, then patch it to admin.
2. **Nobody manages their own account.** Stops the last owner disabling or
   deleting the only account that can recover the realm from the app.

---

## 3. Tenancy — and the bug D4 exposed

First implementation scoped viewers by `ww_invited_by = the onboarding
admin's sub`. **D4 made that wrong.** If admin == customer, then replacing
or deleting that admin orphans every viewer under it: the replacement admin
is a different `sub` and sees none of them. Tenancy must outlive the user
record.

Corrected:

| Attribute | Meaning |
|---|---|
| `ww_tenant` | The access boundary. Minted once (uuid4) when an owner creates an admin. Inherited unchanged by every viewer that admin onboards. |
| `ww_customer` | Human-readable customer name, for the owner's list. Required when creating an admin. |
| `ww_invited_by` | Provenance only. **Not** the access boundary any more. |
| `ww_phone` | `+91XXXXXXXXXX`, normalised at the boundary. Optional. |
| `ww_zone` | Matches `cameras.zone` (free text — no zones table exists yet). |

Scoping rules:

- **owner** → sees and manages every tenant
- **admin** → sees and manages only `ww_tenant == own tenant`, and only
  targets whose direct role ⊆ `{viewer}`
- Out-of-tenant target returns **404, not 403**, so an admin cannot probe for
  the existence of another customer's users

**A viewer's tenant is never read from the request body.** Taking it from
the caller's own claim is what stops an admin injecting a viewer into
another customer's account.

The tenant claim reaches the API via a realm protocol mapper. If the mapper
is missing, `_tenant_of_actor()` falls back to one Admin API lookup of the
caller's own record — degrading to an extra call rather than silently
scoping everyone to `""`, which would merge every customer into one tenant.

---

## 4. Why no password field on the onboarding form

Asking the onboarding admin to set a password destroys non-repudiation:
every action that viewer takes becomes deniable ("my admin knows my
password and could have logged in as me"). Users are created with **no
credential at all** (`test_no_password_is_ever_set` pins this) and Keycloak
emails a `UPDATE_PASSWORD` + `VERIFY_EMAIL` link.

**OPEN (D8):** the real case for asking is no SMTP on a municipal LAN. The
answer there is a *temporary password with forced change at first login* —
admin reads out a one-time string, Keycloak invalidates it on first use —
not a password field. One extra endpoint. **Not built, awaiting decision.**

---

## 5. The SMTP finding

Keycloak sends the onboarding email **itself**, from its own realm SMTP
config. `WW_SMTP_*` only ever reached our reports worker. Without a relay
Keycloak can reach, a user is created successfully and can never sign in —
a failure mode that looks like success in every log we keep.

- **dev / test** — `mailpit` container. Accepts everything on 1025, delivers
  nothing outbound, mail lands in a web inbox (`:8025` dev, `:9025` test).
  The onboarding link is clickable without any risk of mailing a real
  municipal address. Keycloak is health-gated on it so a fresh stack's first
  email is not dropped on a refused connection.
- **prod** — deliberately **no** mailpit; capture-only mail there would
  swallow every onboarding link. Needs a real relay with SPF/DKIM on the
  sending domain — mail from an unauthenticated host on a municipal network
  is filed as spam, which is indistinguishable from "email broken".
  **Realm settings → Email is a one-time console step, documented in
  `docker-compose.prod.yml`, not automated.**

Also found: `keycloak/realm-test.json` **did not exist** while
`docker-compose.test.yml` mounted it, so test Keycloak was starting with an
empty realm and the mount path was silently becoming a directory. Created,
with composite roles, direct access grants enabled, and the service account.

---

## 6. Files

| File | State |
|---|---|
| `backend/app/auth/kc_admin.py` | **new** — Admin REST client, service-account token, deliberately dumb (authorisation lives in the route) |
| `backend/app/api/routes/users.py` | **new** — list / create / edit / enable / delete / resend-setup-email / grantable-roles |
| `backend/app/main.py` | registered `user_routes` at `/api/users` |
| `keycloak/realm-test.json` | **new** — composite roles, confidential client, service account roles, `ww_tenant` mapper, smtp → mailpit, seed owner |
| `backend/tests/test_users_hierarchy.py` | **new** — 39 tests |
| `docker-compose.dev.yml`, `docker-compose.test.yml` | mailpit + keycloak health gate + test client secret |
| `docker-compose.prod.yml`, `.env.example` | realm-SMTP warning |

Keycloak setup the service account requires (else every onboarding call
403s): `realm-management` → `manage-users`, `view-users`, `query-users`,
plus Service Accounts enabled on the client.

Dropped `EmailStr` for a shape regex — Keycloak is the authority on email
validity and rejects a bad address itself, so `email-validator` bought
nothing. (It was separately installed into `backend/.venv`, which was
missing the declared `pydantic[email]` extra and breaking collection of
`test_ws_metadata.py`.)

---

## 7. Test coverage

39 tests. The ones that matter:

- admin requesting `admin` or `owner` → 403 **before any Keycloak call**
  (`created == []`), i.e. the guard is not relying on Keycloak to refuse
- owner requesting `owner` → 403
- `role` absent from `UpdateUserRequest.model_fields`
- viewers inherit their admin's tenant, 3 of them, same tenant
- a viewer's tenant ignores `customer` in the request body
- cross-tenant delete and cross-tenant edit → 404, nothing mutated
- an admin in your own tenant is not manageable by you
- self-management → 403 for both owner and admin
- phone normalises 5 input shapes to `+91XXXXXXXXXX`; 6 bad shapes → 422;
  empty allowed for every role
- full name splits to first/rest; single token leaves surname blank
- account survives a failed setup email (SMTP down must not make a created
  user look like a failed request)

Full backend suite: **274 passed, 12 pre-existing failures** (byte-identical
failure set to before this work).

---

## 8. Open items

| # | Item | Blocking |
|---|---|---|
| D8 | Temp-password fallback for no-SMTP sites? | Decision |
| **T1** | **Tenancy stops at users.** Cameras, events, zones are still realm-wide — an admin from customer A can read customer B's cameras. This is the larger half of D4 and is untouched. | Design |
| D9 | Prod realm SMTP: leave as a console step, or add a bootstrap script? | Decision |
| F1 | `UserManagementView.tsx` — collapsible multi-row add forms, validation, edit + delete, fields name/email/phone/zone (+ customer when creating an admin). Zones read from `/api/cameras` rather than a new vocabulary. | Not started |

### T1 is the important one

D4 says the admin is the customer. Today that is true only of the user
table. Every other scoped resource needs the same `ww_tenant` filter, which
means a `tenant_id` column on cameras (and therefore on everything that
hangs off a camera), plus a dependency that injects the caller's tenant into
every query. Larger than this change was.

---

## 9. Relation model — agreed, not started

Decision: **in the pipeline**, option **A** (probe-side TensorRT runner
invoked from `bag_trigger.py`), running **entirely on GPU against the Gst
buffer, attaching metadata in place** to avoid D2H/H2D copies. Relation
lines drawn through nvosd `NvDsDisplayMeta` **debug-only**, off by default
so evidence stays clean.

Why not an SGIE: `nvinfer` SGIE operates per *object* —
`operate-on-gie-id` hands it one bbox at a time. A relation is a property
of a **pair** (person, bag) and DeepStream has no primitive for that.

Inherited constraints to honour when building it:

1. **VRAM** — shares the 3060 with RF-DETR-Seg. vLLM already failed to fit
   on this box.
2. **Fail-open** — a fault in the runner must not kill streaming. On error,
   fall back to the dwell-based FSM verdict. Silently suppressing
   violations when a model fails is the failure mode nobody notices.
3. **Engine loading** — load from the bind-mounted `models/` dir like
   `rfdetr_seg`, so a model swap is a container restart, not a 30 GB image
   rebuild.
