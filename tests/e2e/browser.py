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
    try:
        import httpx
        r = httpx.post(f"{cfg.base_url}/api/auth/login", json={"username": cfg.username, "password": cfg.password}, verify=cfg.verify_tls, timeout=5)
        if r.status_code == 200 and r.cookies:
            ctx = page.context
            cookies = []
            for k, v in r.cookies.items():
                cookies.append({"name": k, "value": v, "url": cfg.base_url})
            try:
                ctx.add_cookies(cookies)
                page.goto(cfg.base_url + "/", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector("#username", state="detached", timeout=5000)
                    return
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass

    page.goto(cfg.base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#username", timeout=20000)
    page.fill("#username", cfg.username)
    page.fill("#password", cfg.password)
    page.click('button[type="submit"]')
    try:
        page.wait_for_selector("#username", state="detached", timeout=10000)
        return
    except Exception:
        pass
    try:
        page.wait_for_selector('text="Failed to fetch"', timeout=2000)
    except Exception:
        pass
    raise AssertionError(f"Login failed on {cfg.base_url}: {page.url} :: {page.inner_text('body')[:200]}")
