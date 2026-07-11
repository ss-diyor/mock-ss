import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import BaseModel


DEFAULT_PRIMARY_COLOR = "#1a56e8"
DEFAULT_SECONDARY_COLOR = "#0b1733"
ORGANIZATION_TYPES = {"learning_center", "school"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class BrandingUpdateIn(BaseModel):
    brand_name: Optional[str] = None
    slug: Optional[str] = None
    primary_color: str = DEFAULT_PRIMARY_COLOR
    secondary_color: str = DEFAULT_SECONDARY_COLOR
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    show_powered_by: bool = True


def clean_optional(value: Optional[str], max_length: int = 255) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"Qiymat {max_length} belgidan oshmasligi kerak")
    return value


def validate_branding(data: BrandingUpdateIn) -> dict:
    brand_name = clean_optional(data.brand_name, 120)
    slug = clean_optional(data.slug, 80)
    if slug:
        slug = slug.lower()
        if not SLUG_RE.fullmatch(slug):
            raise HTTPException(status_code=400, detail="Slug faqat kichik harf, raqam va tirelardan iborat bo'lishi kerak")
    if not HEX_COLOR_RE.fullmatch(data.primary_color):
        raise HTTPException(status_code=400, detail="Asosiy rang #RRGGBB formatida bo'lishi kerak")
    if not HEX_COLOR_RE.fullmatch(data.secondary_color):
        raise HTTPException(status_code=400, detail="Ikkinchi rang #RRGGBB formatida bo'lishi kerak")

    logo_url = clean_optional(data.logo_url, 500)
    favicon_url = clean_optional(data.favicon_url, 500)
    for url in (logo_url, favicon_url):
        if not url:
            continue
        if any(char in url for char in ('"', "'", "<", ">", " ", "\n", "\r", "\t")):
            raise HTTPException(status_code=400, detail="Logo yoki favicon manzili noto'g'ri")
        if url.startswith("/"):
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Logo va favicon HTTPS yoki lokal / manzil bo'lishi kerak")

    return {
        "brand_name": brand_name,
        "slug": slug,
        "brand_primary_color": data.primary_color.lower(),
        "brand_secondary_color": data.secondary_color.lower(),
        "brand_logo_url": logo_url,
        "brand_favicon_url": favicon_url,
        "brand_contact_email": clean_optional(data.contact_email, 254),
        "brand_contact_phone": clean_optional(data.contact_phone, 40),
        "show_powered_by": data.show_powered_by,
    }


def branding_payload(row) -> dict:
    organization_type = row["organization_type"] or "learning_center"
    fallback_name = "Maktab" if organization_type == "school" else "O'quv markazi"
    return {
        "organization_id": row["id"],
        "organization_type": organization_type,
        "organization_name": row["name"],
        "brand_name": row["brand_name"] or row["name"] or fallback_name,
        "slug": row["slug"],
        "primary_color": row["brand_primary_color"] or DEFAULT_PRIMARY_COLOR,
        "secondary_color": row["brand_secondary_color"] or DEFAULT_SECONDARY_COLOR,
        "logo_url": row["brand_logo_url"],
        "favicon_url": row["brand_favicon_url"],
        "contact_email": row["brand_contact_email"],
        "contact_phone": row["brand_contact_phone"],
        "show_powered_by": row["show_powered_by"] is not False,
    }
