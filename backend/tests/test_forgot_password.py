"""Tests for the public POST /auth/forgot-password endpoint.

This endpoint is anti-enumeration: it must return 202 for every submitted
username regardless of whether the account exists, and it must swallow all
kc_admin / SMTP errors so an anonymous caller can never use it as a
user-enumeration or SMTP-probe oracle.
"""
import os
import tempfile

# The app builds its DB engine at import time from WW_DATABASE_URL. These
# tests never touch the database (kc_admin is fully mocked), so any parseable
# async URL suffices to let create_app() succeed. Set before app import.
os.environ.setdefault(
    "WW_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)
# Static-file roots default to in-container paths (/app/...) that don't exist
# outside Docker; point them at writable temp dirs so create_app() succeeds.
_tmp = tempfile.mkdtemp(prefix="ww-forgot-pw-test-")
os.environ.setdefault("WW_EVIDENCE_DIR", os.path.join(_tmp, "evidence"))
os.environ.setdefault("WW_TTS_DIR", os.path.join(_tmp, "tts"))

from unittest.mock import AsyncMock, patch  # noqa: E402


async def test_forgot_password_existing_user_returns_202(client):
    """A real user with an email triggers a reset email and gets 202."""
    find = AsyncMock(return_value={"id": "u-123", "email": "a@example.com"})
    send = AsyncMock(return_value=None)
    with patch("app.api.routes.auth.kc_admin.find_user_by_username", find), patch(
        "app.api.routes.auth.kc_admin.send_password_reset_email", send
    ):
        resp = await client.post("/api/auth/forgot-password", json={"username": "alice"})

    assert resp.status_code == 202
    find.assert_awaited_once()
    send.assert_awaited_once_with("u-123")


async def test_forgot_password_nonexistent_user_returns_202(client):
    """An unknown username returns 202 and sends no email."""
    find = AsyncMock(return_value=None)
    send = AsyncMock(return_value=None)
    with patch("app.api.routes.auth.kc_admin.find_user_by_username", find), patch(
        "app.api.routes.auth.kc_admin.send_password_reset_email", send
    ):
        resp = await client.post("/api/auth/forgot-password", json={"username": "ghost"})

    assert resp.status_code == 202
    find.assert_awaited_once()
    send.assert_not_awaited()


async def test_forgot_password_no_enumeration_difference(client):
    """Existing and nonexistent usernames must be indistinguishable to the
    caller: identical status code and identical (empty) response body."""
    with patch(
        "app.api.routes.auth.kc_admin.find_user_by_username",
        AsyncMock(return_value={"id": "u-1", "email": "x@example.com"}),
    ), patch("app.api.routes.auth.kc_admin.send_password_reset_email", AsyncMock()):
        existing = await client.post(
            "/api/auth/forgot-password", json={"username": "real"}
        )

    with patch(
        "app.api.routes.auth.kc_admin.find_user_by_username",
        AsyncMock(return_value=None),
    ), patch("app.api.routes.auth.kc_admin.send_password_reset_email", AsyncMock()):
        missing = await client.post(
            "/api/auth/forgot-password", json={"username": "fake"}
        )

    assert existing.status_code == missing.status_code == 202
    assert existing.content == missing.content


async def test_forgot_password_swallows_lookup_error(client):
    """A kc_admin lookup failure must not surface — still 202."""
    find = AsyncMock(side_effect=RuntimeError("keycloak down"))
    with patch("app.api.routes.auth.kc_admin.find_user_by_username", find):
        resp = await client.post("/api/auth/forgot-password", json={"username": "alice"})

    assert resp.status_code == 202
    find.assert_awaited_once()


async def test_forgot_password_swallows_send_error(client):
    """An SMTP / send-email failure must not surface — still 202."""
    find = AsyncMock(return_value={"id": "u-9", "email": "a@example.com"})
    send = AsyncMock(side_effect=RuntimeError("smtp unconfigured"))
    with patch("app.api.routes.auth.kc_admin.find_user_by_username", find), patch(
        "app.api.routes.auth.kc_admin.send_password_reset_email", send
    ):
        resp = await client.post("/api/auth/forgot-password", json={"username": "alice"})

    assert resp.status_code == 202
    send.assert_awaited_once()


async def test_forgot_password_user_without_email_sends_nothing(client):
    """A user record with no email address must not trigger a send, but
    still returns 202 (no enumeration signal)."""
    find = AsyncMock(return_value={"id": "u-noemail"})
    send = AsyncMock()
    with patch("app.api.routes.auth.kc_admin.find_user_by_username", find), patch(
        "app.api.routes.auth.kc_admin.send_password_reset_email", send
    ):
        resp = await client.post("/api/auth/forgot-password", json={"username": "bob"})

    assert resp.status_code == 202
    send.assert_not_awaited()
