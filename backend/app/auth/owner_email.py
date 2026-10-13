"""Send the owner welcome/account-ready email via the app's own SMTP path.

Keycloak's execute-actions-email (the activation link) goes out through the
realm mailer; in practice that HTML message is unreliable to external inboxes
on this deployment. This sender uses the same aiosmtplib path the app already
uses, which delivers, so owner seeding can hand the new owner working access
(URL + username + temporary password) directly.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from app.config.settings import settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


def _render(name: str, app_url: str, username: str, temp_password: str) -> str:
    text = (_TEMPLATE_DIR / name).read_text()
    return (
        text.replace("__APP_URL__", app_url)
        .replace("__USERNAME__", username)
        .replace("__TEMP_PASSWORD__", temp_password)
    )


def send_owner_welcome(
    *, to_email: str, app_url: str, username: str, temp_password: str
) -> None:
    """Send the account-ready email. Raises on SMTP failure so the caller
    can report it (owner seeding wants to know if the mail didn't go)."""
    if not settings.smtp_host:
        raise RuntimeError("SMTP not configured (smtp_host empty)")

    # Plain text ONLY. The multipart/HTML variant is silently dropped by the
    # mail host / Gmail on this deployment (the plain text reliably delivers),
    # so correctness beats the fancier template. The .html template is kept
    # for when the domain gets SPF/DKIM and HTML mail can be trusted again.
    msg = EmailMessage()
    msg["Subject"] = "Your Civilytics account is ready"
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(_render("account_ready.txt", app_url, username, temp_password))

    # Port 587 is STARTTLS (not implicit TLS). The cert on mail hosts is often
    # self-signed, so don't hard-fail verification here.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    s = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    try:
        s.ehlo()
        if settings.smtp_tls:
            s.starttls(context=ctx)
            s.ehlo()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    logger.info("Owner welcome email sent to %s", to_email)
