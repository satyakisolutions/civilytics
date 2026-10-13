"""Playwright helpers. Importing this is safe even without playwright."""
from __future__ import annotations

import importlib.util

HAVE_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def browser_available() -> bool:
    """True if playwright AND a chromium build are installed."""
    if not HAVE_PLAYWRIGHT:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        import os
        return bool(path) and os.path.exists(path)
    except Exception:  # noqa: BLE001
        return False


def login_page(page, cfg):
    """Drive the inline login form (no /login route) and land on the app."""
    page.goto(cfg.base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#username", timeout=20000)
    page.fill("#username", cfg.username)
    page.fill("#password", cfg.password)
    page.click('button[type="submit"]')
    # Login form gone == authenticated app rendered.
    page.wait_for_selector("#username", state="detached", timeout=20000)
