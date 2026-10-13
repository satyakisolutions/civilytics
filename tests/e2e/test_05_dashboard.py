"""Step 5 + dashboard update latency — alert appears in the live panel (DOM).

Log in, land on the monitoring dashboard (/), publish a synthetic alert to
`wastewatch.alerts`, and wait for its unique title to render in the live alerts
panel (AlertPanel → .alert-card-*). Measures publish→DOM latency.

Depends on the alert reaching the browser over /ws/alerts (useAlerts) — that
path works even though DB persistence is broken (see test_04).
"""
import time
import uuid

import pytest

from browser import browser_available, login_page
from helpers import publish, kafka_reachable

pytestmark = [pytest.mark.e2e, pytest.mark.browser]

_skip = pytest.mark.skipif(not browser_available(),
                           reason="playwright/chromium not installed")


@_skip
def test_alert_appears_on_dashboard(stack, make_camera, capsys):
    if not kafka_reachable(stack.kafka):
        pytest.skip(f"kafka not reachable at {stack.kafka}")
    from playwright.sync_api import sync_playwright

    cam = make_camera()
    title = f"E2E DOM {uuid.uuid4().hex[:6]}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        try:
            login_page(page, stack)
            page.goto(stack.base_url + "/", wait_until="networkidle")
            page.wait_for_timeout(1500)  # let /ws/alerts connect

            t0 = time.time()
            publish("wastewatch.alerts", {
                "camera_id": cam["id"], "severity": "critical", "title": title,
                "message": "synthetic dashboard alert", "track_id": 1,
                "suppress_tts": True, "detected_at": t0,
            }, stack.kafka)

            try:
                page.wait_for_selector(f"text={title}",
                                       timeout=int(stack.dashboard_latency_s * 1000))
            except Exception:
                pytest.fail(f"alert '{title}' did not render on dashboard within "
                            f"{stack.dashboard_latency_s}s")
            latency = time.time() - t0
            with capsys.disabled():
                print(f"\n[latency] alert publish → dashboard DOM: {latency:.2f} s")
            assert latency < stack.dashboard_latency_s
        finally:
            ctx.close(); browser.close()
