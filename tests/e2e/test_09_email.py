"""Step 9 — exercise an email path (forgot-password).

Live stack: the API accepts the request (always 202 to avoid user enumeration);
we cannot read the real inbox, so delivery is best-effort / not asserted.
Non-live (testcontainers): a Mailpit container would let us assert arrival —
wired via CIVILYTICS_MAILPIT_URL when present.
"""
import os

import httpx
import pytest

pytestmark = pytest.mark.e2e


def test_forgot_password_accepted(client, stack):
    r = client.post("/api/auth/forgot-password", json={"username": stack.username})
    assert r.status_code == 202


def test_email_delivered_via_mailpit():
    url = os.environ.get("CIVILYTICS_MAILPIT_URL")
    if not url:
        pytest.skip("no CIVILYTICS_MAILPIT_URL (live stack: delivery is best-effort)")
    # Mailpit API: /api/v1/messages
    msgs = httpx.get(f"{url.rstrip('/')}/api/v1/messages", timeout=10).json()
    assert msgs.get("total", 0) >= 1
