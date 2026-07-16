"""Email provider abstraction for Resend, Mailtrap SMTP, or disabled mode."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

import httpx


PROVIDERS = {"resend", "mailtrap", "smtp", "disabled"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _provider() -> str:
    provider = os.environ.get("EMAIL_PROVIDER", "resend").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError("EMAIL_PROVIDER resend, mailtrap, smtp yoki disabled bo'lishi kerak")
    return provider


def _sender() -> tuple[str, str]:
    name = os.environ.get("EMAIL_FROM_NAME", "IELTS Mock SS").strip() or "IELTS Mock SS"
    address = os.environ.get("EMAIL_FROM", "noreply@ielts.sultanov.space").strip()
    if not address or "@" not in address or any(char in address for char in "\r\n"):
        raise RuntimeError("EMAIL_FROM noto'g'ri sozlangan")
    return name, address


def _clean_header(value: str, fallback: str = "") -> str:
    return " ".join(str(value or fallback).replace("\r", " ").replace("\n", " ").split())


def _smtp_settings() -> dict:
    host = os.environ.get("SMTP_HOST", "").strip()
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    try:
        port = int(os.environ.get("SMTP_PORT", "2525"))
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT butun son bo'lishi kerak") from exc
    if not host or not username or not password:
        raise RuntimeError("SMTP_HOST, SMTP_USERNAME va SMTP_PASSWORD sozlanishi kerak")
    if not 1 <= port <= 65535:
        raise RuntimeError("SMTP_PORT noto'g'ri")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": _env_bool("SMTP_USE_TLS", True),
        "use_ssl": _env_bool("SMTP_USE_SSL", False),
    }


def _build_message(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
) -> EmailMessage:
    from_name, from_email = _sender()
    message = EmailMessage()
    message["From"] = formataddr((_clean_header(from_name), from_email))
    message["To"] = formataddr((_clean_header(to_name), to_email.strip()))
    message["Subject"] = _clean_header(subject, "IELTS Mock SS")
    message.set_content("Bu HTML email. Uni ko'rish uchun HTML qo'llab-quvvatlaydigan email dasturidan foydalaning.")
    message.add_alternative(html_body, subtype="html")
    if attachment_bytes is not None and attachment_filename:
        mime = mimetypes.guess_type(attachment_filename)[0] or "application/octet-stream"
        maintype, subtype = mime.split("/", 1)
        message.add_attachment(
            attachment_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=_clean_header(attachment_filename, "attachment"),
        )
    return message


def _send_smtp_sync(message: EmailMessage):
    settings = _smtp_settings()
    context = ssl.create_default_context()
    client_class = smtplib.SMTP_SSL if settings["use_ssl"] else smtplib.SMTP
    with client_class(settings["host"], settings["port"], timeout=20) as client:
        client.ehlo()
        if settings["use_tls"] and not settings["use_ssl"]:
            client.starttls(context=context)
            client.ehlo()
        client.login(settings["username"], settings["password"])
        client.send_message(message)


async def _send_resend(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY sozlanmagan")
    from_name, from_email = _sender()
    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "subject": _clean_header(subject, "IELTS Mock SS"),
        "html": html_body,
    }
    if attachment_bytes is not None and attachment_filename:
        payload["attachments"] = [{
            "filename": _clean_header(attachment_filename, "attachment"),
            "content": base64.b64encode(attachment_bytes).decode("ascii"),
        }]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Resend xatosi: {response.text[:1000]}")


async def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
):
    provider = _provider()
    if provider == "disabled":
        return {"provider": provider, "skipped": True}
    if provider == "resend":
        await _send_resend(to_email, to_name, subject, html_body)
    else:
        message = _build_message(to_email, to_name, subject, html_body)
        await asyncio.to_thread(_send_smtp_sync, message)
    return {"provider": provider, "skipped": False}


async def send_email_with_attachment(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    attachment_bytes: bytes,
    attachment_filename: str,
):
    provider = _provider()
    if provider == "disabled":
        return {"provider": provider, "skipped": True}
    if provider == "resend":
        await _send_resend(
            to_email, to_name, subject, html_body, attachment_bytes, attachment_filename
        )
    else:
        message = _build_message(
            to_email, to_name, subject, html_body, attachment_bytes, attachment_filename
        )
        await asyncio.to_thread(_send_smtp_sync, message)
    return {"provider": provider, "skipped": False}
