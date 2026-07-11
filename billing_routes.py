import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth import get_current_head_teacher
from db import get_pool


router = APIRouter(prefix="/api/billing", tags=["billing"])
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


@router.get("/status")
async def billing_status(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        center = await conn.fetchrow(
            "SELECT id, subscription_required FROM centers WHERE id=$1", current_user["center_id"]
        )
        if not center:
            raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
        plans = await conn.fetch(
            "SELECT id, code, name, price_monthly, price_yearly FROM subscription_plans WHERE is_active=TRUE ORDER BY price_monthly"
        )
        subscription = await conn.fetchrow(
            """
            SELECT s.status, s.trial_ends_at, s.current_period_start, s.current_period_end,
                   s.grace_ends_at, p.name AS plan_name
            FROM organization_subscriptions s
            LEFT JOIN subscription_plans p ON p.id=s.plan_id
            WHERE s.center_id=$1
            """,
            center["id"]
        )
        payments = await conn.fetch(
            """
            SELECT sp.id, sp.order_code, sp.billing_cycle, sp.amount, sp.status,
                   sp.review_note, sp.created_at, p.name AS plan_name
            FROM subscription_payments sp JOIN subscription_plans p ON p.id=sp.plan_id
            WHERE sp.center_id=$1 ORDER BY sp.created_at DESC LIMIT 20
            """,
            center["id"]
        )
    serialize_date = lambda value: value.isoformat() if value else None
    return {
        "subscription_required": center["subscription_required"],
        "payment_details": {
            "card_number": os.environ.get("MANUAL_PAYMENT_CARD", ""),
            "card_holder": os.environ.get("MANUAL_PAYMENT_HOLDER", ""),
            "instructions": os.environ.get("MANUAL_PAYMENT_INSTRUCTIONS", "To'lovdan so'ng tasdiqlovchi faylni yuklang."),
        },
        "plans": [dict(row) for row in plans],
        "subscription": ({
            **dict(subscription),
            "trial_ends_at": serialize_date(subscription["trial_ends_at"]),
            "current_period_start": serialize_date(subscription["current_period_start"]),
            "current_period_end": serialize_date(subscription["current_period_end"]),
            "grace_ends_at": serialize_date(subscription["grace_ends_at"]),
        } if subscription else None),
        "payments": [dict(row) | {"created_at": row["created_at"].isoformat()} for row in payments],
    }


@router.post("/payments")
async def submit_payment(
    plan_id: int = Form(...),
    billing_cycle: str = Form(...),
    payer_name: str = Form(...),
    transaction_reference: str = Form(...),
    receipt: UploadFile = File(...),
    current_user: dict = Depends(get_current_head_teacher)
):
    if billing_cycle not in {"monthly", "yearly"}:
        raise HTTPException(status_code=400, detail="To'lov davri noto'g'ri")
    payer_name = payer_name.strip()
    transaction_reference = transaction_reference.strip()
    if len(payer_name) < 3 or len(payer_name) > 120:
        raise HTTPException(status_code=400, detail="To'lovchi ismini kiriting")
    if len(transaction_reference) < 3 or len(transaction_reference) > 120:
        raise HTTPException(status_code=400, detail="Tranzaksiya raqamini kiriting")
    if receipt.content_type not in ALLOWED_RECEIPT_TYPES:
        raise HTTPException(status_code=400, detail="Tasdiqlovchi fayl JPG, PNG, WEBP yoki PDF bo'lishi kerak")
    receipt_data = await receipt.read(MAX_RECEIPT_BYTES + 1)
    if len(receipt_data) > MAX_RECEIPT_BYTES:
        raise HTTPException(status_code=413, detail="Tasdiqlovchi fayl 5 MB dan oshmasligi kerak")
    if not receipt_data:
        raise HTTPException(status_code=400, detail="Tasdiqlovchi fayl bo'sh")

    db = await get_pool()
    async with db.acquire() as conn:
        center = await conn.fetchrow(
            "SELECT id, subscription_required FROM centers WHERE id=$1", current_user["center_id"]
        )
        if not center or not center["subscription_required"]:
            raise HTTPException(status_code=403, detail="Bu tashkilot uchun obuna to'lovi yoqilmagan")
        plan = await conn.fetchrow(
            "SELECT id, price_monthly, price_yearly FROM subscription_plans WHERE id=$1 AND is_active=TRUE", plan_id
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Tarif topilmadi")
        duplicate = await conn.fetchval(
            "SELECT 1 FROM subscription_payments WHERE transaction_reference=$1", transaction_reference
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Bu tranzaksiya raqami oldin yuborilgan")
        amount = plan["price_yearly"] if billing_cycle == "yearly" else plan["price_monthly"]
        order_code = f"SUB-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}"
        row = await conn.fetchrow(
            """
            INSERT INTO subscription_payments(
                center_id, plan_id, billing_cycle, amount, order_code, payer_name,
                transaction_reference, receipt_data, receipt_mime
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id, order_code, status
            """,
            center["id"], plan_id, billing_cycle, amount, order_code, payer_name,
            transaction_reference, receipt_data, receipt.content_type
        )
    return dict(row) | {"message": "To'lov tasdiqlash uchun yuborildi"}
