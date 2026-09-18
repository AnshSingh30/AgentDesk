"""Transactional email: Resend API in production, SMTP (Mailpit) locally (PRD NT-1)."""

from __future__ import annotations

import asyncio
import html
import smtplib
from email.message import EmailMessage

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger()


async def send_email(to: str, subject: str, body_html: str, body_text: str | None = None) -> None:
    if settings.resend_api_key:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.email_from,
                    "to": [to],
                    "subject": subject,
                    "html": body_html,
                    "text": body_text or "",
                },
            )
            r.raise_for_status()
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text or "This email requires an HTML-capable client.")
    msg.add_alternative(body_html, subtype="html")

    def _send() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
            s.send_message(msg)

    try:
        await asyncio.to_thread(_send)
    except OSError as e:  # local dev without Mailpit must not break signup
        log.warning("email_send_failed", to=to, subject=subject, error=str(e))


def _layout(title: str, lines: list[str], cta_text: str, cta_url: str) -> str:
    paragraphs = "".join(f"<p style='margin:0 0 12px'>{html.escape(line)}</p>" for line in lines)
    return f"""
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:auto;padding:24px;color:#111">
  <h2 style="margin:0 0 16px">{html.escape(title)}</h2>
  {paragraphs}
  <p style="margin:20px 0"><a href="{html.escape(cta_url)}" style="background:#0b0b0b;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none">{html.escape(cta_text)}</a></p>
  <p style="color:#666;font-size:12px">If the button doesn't work, open this link: {html.escape(cta_url)}</p>
</div>"""


async def send_verification_email(to: str, name: str, token: str) -> None:
    url = f"{settings.app_url}/verify-email?token={token}"
    await send_email(
        to,
        f"Verify your email for {settings.app_name}",
        _layout(
            "Verify your email",
            [f"Hi {name},", "Confirm your email address to start building agents."],
            "Verify email",
            url,
        ),
        f"Verify your email: {url}",
    )


async def send_password_reset_email(to: str, name: str, token: str) -> None:
    url = f"{settings.app_url}/reset-password?token={token}"
    await send_email(
        to,
        f"Reset your {settings.app_name} password",
        _layout(
            "Reset your password",
            [f"Hi {name},", "This link expires in 30 minutes and can be used once."],
            "Reset password",
            url,
        ),
        f"Reset your password: {url}",
    )


async def send_invitation_email(to: str, org_name: str, role: str, token: str) -> None:
    url = f"{settings.app_url}/invite/{token}"
    await send_email(
        to,
        f"You're invited to {org_name} on {settings.app_name}",
        _layout(
            "You have been invited",
            [f"You've been invited to join {org_name} as {role}.", "The invitation expires in 7 days."],
            "Accept invitation",
            url,
        ),
        f"Accept your invitation: {url}",
    )
