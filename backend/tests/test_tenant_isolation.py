"""Per-tenant data isolation (T1).

No live DB in the test harness, so row isolation is proved at the statement
level: the SQL a scoped tenant's request issues carries a
`tenant_id = <their uuid>` predicate (so it can never match another tenant's
rows), while the tenantless platform owner's request carries none (so it reads
across tenants). Plus pure-unit coverage of the tenancy helpers.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.auth.middleware import get_current_user
from app.auth.tenancy import DEFAULT_TENANT_ID, _tenant_claim, scope, tenant_for_write
from app.db.models import Alert, Camera, EvidenceFile, ViolationEvent

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _compile(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── Pure helpers ────────────────────────────────────────────────────

def test_scope_adds_predicate_when_tenant_set():
    sql = _compile(scope(select(Camera), Camera, TENANT_A))
    assert "tenant_id =" in sql.lower()
    assert TENANT_A in sql


def test_scope_omits_predicate_for_owner():
    sql = _compile(scope(select(Camera), Camera, None))
    assert "where" not in sql.lower()


def test_tenant_for_write_uses_caller_tenant():
    assert tenant_for_write(TENANT_A) == uuid.UUID(TENANT_A)


def test_tenant_for_write_defaults_for_owner():
    assert tenant_for_write(None) == DEFAULT_TENANT_ID


def test_tenant_claim_reads_attribute_shape():
    assert _tenant_claim({"attributes": {"ww_tenant": [TENANT_A]}}) == TENANT_A


def test_tenant_claim_reads_top_level_jwt_claim():
    assert _tenant_claim({"ww_tenant": TENANT_A}) == TENANT_A


def test_tenant_claim_none_for_owner():
    assert _tenant_claim({"preferred_username": "owner"}) is None


# ── Route-level isolation ───────────────────────────────────────────

class _CapturingResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


@pytest.fixture
def harness(app):
    """(client, captured-statements) with get_db mocked and a swappable user."""
    from app.api.deps import get_db

    captured: list = []
    added: list = []

    async def _db():
        class _DB:
            async def execute(self, stmt, *a, **k):
                captured.append(stmt)
                return _CapturingResult()

            def add(self, obj):
                added.append(obj)

            async def flush(self):
                pass

            async def commit(self):
                pass

            async def refresh(self, obj):
                pass

        yield _DB()

    app.dependency_overrides[get_db] = _db
    yield app, captured, added
    app.dependency_overrides.clear()


def _as_user(app, user: dict):
    async def _override():
        return user
    app.dependency_overrides[get_current_user] = _override


def _admin(tenant: str | None):
    u = {"sub": "u", "preferred_username": "a", "realm_access": {"roles": ["admin", "viewer"]}}
    if tenant is not None:
        u["ww_tenant"] = tenant
    return u


async def _get(app, path):
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as ac:
        return await ac.get(path)


@pytest.mark.parametrize(
    "path, model",
    [
        ("/api/cameras", Camera),
        ("/api/events", ViolationEvent),
        ("/api/alerts", Alert),
        ("/api/evidence", EvidenceFile),
    ],
)
@pytest.mark.asyncio
async def test_scoped_admin_query_is_tenant_filtered(harness, path, model):
    app, captured, added = harness
    _as_user(app, _admin(TENANT_A))
    resp = await _get(app, path)
    assert resp.status_code == 200
    sql = " ".join(_compile(s) for s in captured)
    # The list query is scoped to tenant A -- it can never match tenant B.
    assert TENANT_A in sql
    assert TENANT_B not in sql


@pytest.mark.parametrize("path", ["/api/cameras", "/api/events", "/api/alerts", "/api/evidence"])
@pytest.mark.asyncio
async def test_owner_query_is_unscoped(harness, path):
    app, captured, added = harness
    _as_user(app, _admin(None))  # tenantless platform owner
    resp = await _get(app, path)
    assert resp.status_code == 200
    sql = " ".join(_compile(s) for s in captured).lower()
    assert "tenant_id =" not in sql


@pytest.mark.asyncio
async def test_scoped_insert_stamps_caller_tenant(harness):
    app, captured, added = harness
    _as_user(app, _admin(TENANT_A))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as ac:
        await ac.post(
            "/api/cameras",
            json={"name": "Cam", "zone": "Z", "stream_url": "rtsp://x"},
        )
    cams = [o for o in added if isinstance(o, Camera)]
    assert cams, "a Camera was inserted"
    assert cams[0].tenant_id == uuid.UUID(TENANT_A)
