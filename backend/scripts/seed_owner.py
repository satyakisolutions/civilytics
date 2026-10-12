#!/usr/bin/env python3
"""Bootstrap / manage the first owner (superadmin) account in Keycloak.

There is no in-app signup: role authority lives entirely in Keycloak
(see app/db/models.py), and POST /users requires an existing owner/admin
to call it. This script breaks that chicken-and-egg, by talking to the
Keycloak Admin REST API directly (same helpers as kc_admin.py).

Two modes:
  - Non-interactive (default, e.g. from start.sh's headless exec -T):
    no-op if an owner already exists. Otherwise creates one from
    WW_SEED_OWNER_* env vars / --username etc, failing if no password
    is available (WW_SEED_OWNER_PASSWORD unset).
  - --interactive (TTY attached, e.g. `docker compose exec -it`):
    shows who the current owner(s) are, then asks whether to keep them,
    edit one (reset password), or add a new owner. Always exits 0 once
    the operator is done, never silently skips.

Usage:
    WW_SEED_OWNER_PASSWORD=... python backend/scripts/seed_owner.py
    python backend/scripts/seed_owner.py --interactive
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import kc_admin  # noqa: E402


def _describe(u: dict) -> str:
    return f"{u.get('username')} <{u.get('email') or 'no email'}> (id={u['id']})"


async def _create(username, email, first_name, last_name, tenant, customer, password) -> str:
    user_id = await kc_admin.create_user(
        username=username, email=email, first_name=first_name, last_name=last_name,
        invited_by="bootstrap", tenant=tenant, customer=customer,
    )
    await kc_admin.assign_realm_role(user_id, "owner")
    # Non-temporary + required actions cleared: this app's login (auth.py)
    # is Direct Access Grant only, with no redirect-flow step to satisfy
    # UPDATE_PASSWORD/VERIFY_EMAIL or a temporary credential. Either one
    # left in place makes Keycloak refuse the login outright.
    await kc_admin.set_password(user_id, password, temporary=False)
    await kc_admin.clear_required_actions(user_id)
    print(f"created owner '{username}' (id={user_id})")
    return user_id


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


async def run_interactive(args) -> int:
    existing = await kc_admin.users_with_role("owner")
    if existing:
        print(f"Current owner(s):")
        for i, u in enumerate(existing, 1):
            print(f"  {i}. {_describe(u)}")
        choice = input("\n[k]eep as is / [a]dd new owner / [e]dit one (reset password)? [k]: ").strip().lower() or "k"
    else:
        print("No owner exists yet.")
        choice = "a"

    if choice == "k":
        print("No changes.")
        return 0

    if choice == "e":
        if not existing:
            print("Nothing to edit — no owner exists.")
            return 0
        idx = 0
        if len(existing) > 1:
            idx = int(_prompt("Which number", "1")) - 1
        target = existing[idx]
        new_password = getpass.getpass(f"New password for {target['username']}: ")
        if not new_password:
            print("Empty password — aborted.")
            return 1
        await kc_admin.set_password(target["id"], new_password, temporary=False)
        await kc_admin.clear_required_actions(target["id"])
        print(f"password reset for '{target['username']}'")
        return 0

    if choice == "a":
        username = _prompt("Username", args.username)
        email = _prompt("Email", args.email)
        first_name = _prompt("First name", args.first_name)
        last_name = _prompt("Last name", args.last_name)
        tenant = _prompt("Tenant", args.tenant)
        password = getpass.getpass("Password: ")
        if not password:
            print("Empty password — aborted.")
            return 1
        await _create(username, email, first_name, last_name, tenant, args.customer, password)
        return 0

    print(f"unrecognized choice: {choice!r}")
    return 1


async def run_noninteractive(args, password: str | None) -> int:
    existing = await kc_admin.users_with_role("owner")
    if existing:
        print(f"owner already exists ({existing[0].get('username')}) -- skipping")
        return 0
    if not password:
        print("WW_SEED_OWNER_PASSWORD not set -- refusing to seed an owner with no password", file=sys.stderr)
        return 1
    await _create(args.username, args.email, args.first_name, args.last_name,
                  args.tenant, args.customer, password)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--username", default=os.environ.get("WW_SEED_OWNER_USERNAME", "admin"))
    ap.add_argument("--email", default=os.environ.get("WW_SEED_OWNER_EMAIL", "admin@example.com"))
    ap.add_argument("--first-name", default=os.environ.get("WW_SEED_OWNER_FIRST_NAME", "Super"))
    ap.add_argument("--last-name", default=os.environ.get("WW_SEED_OWNER_LAST_NAME", "Admin"))
    ap.add_argument("--tenant", default=os.environ.get("WW_SEED_OWNER_TENANT", "default"))
    ap.add_argument("--customer", default=os.environ.get("WW_SEED_OWNER_CUSTOMER", ""))
    args = ap.parse_args()

    if args.interactive:
        return asyncio.run(run_interactive(args))
    return asyncio.run(run_noninteractive(args, os.environ.get("WW_SEED_OWNER_PASSWORD")))


if __name__ == "__main__":
    raise SystemExit(main())
