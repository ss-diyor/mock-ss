"""Extra routes for IELTS Mock SS pitch-deck features.

Install:
1. Copy this file into the project root.
2. Add this to main.py near the other imports:
       from feature_routes import router as feature_router
3. Add this after app.include_router(auth_router):
       app.include_router(feature_router)
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from db import get_pool
from auth import get_current_user
from mock_catalog import get_test_by_id, public_test_catalog
from scoring import calculate_overall_band, get_band_score


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


router = APIRouter()


@router.get("/legacy-test-bank")
async def test_bank_page():
   return FileResponse(STATIC_DIR / "test-bank.html")


@router.get("/speaking-demo")
async def speaking_demo_page():
    return FileResponse(STATIC_DIR / "speaking-demo.html")


@router.get("/api/legacy-tests")
async def list_tests():
    return {"tests": public_test_catalog()}


@router.get("/api/legacy-tests/{test_id}")
async def get_test(test_id: str):
    test = get_test_by_id(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


@router.get("/api/dashboard")
async def user_dashboard(current_user: dict = Depends(get_current_user)):
    clean_email = current_user["email"].strip().lower()

    db = await get_pool()
    async with db.acquire() as conn:
        user = await conn.fetchrow(
            """
            SELECT username, email, full_name, created_at, email_verified, referral_count
            FROM users
            WHERE email = $1
            """,
            clean_email,
        )

        rows = await conn.fetch(
            """
            SELECT er.section, er.score, er.total, er.writing_band, er.writing_feedback, er.submitted_at,
                   COALESCE(t.title, er.test_slug, 'IELTS Mock SS') AS test_title
            FROM exam_results er LEFT JOIN tests t ON t.id=er.test_id
            WHERE er.email = $1
            ORDER BY submitted_at DESC
            """,
            clean_email,
        )

    attempts_by_section: dict[str, int] = defaultdict(int)
    latest_by_section: dict[str, dict] = {}
    history: list[dict] = []

    for row in rows:
        section = row["section"]
        attempts_by_section[section] += 1

        if section == "writing":
            band = float(row["writing_band"]) if row["writing_band"] is not None else None
        elif row["score"] is not None and row["total"] is not None:
            band = get_band_score(row["score"], row["total"], section)
        else:
            band = None

        item = {
            "section": section,
            "score": row["score"],
            "total": row["total"],
            "band": band,
            "writing_feedback": row["writing_feedback"],
            "test_title": row["test_title"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
        }
        history.append(item)
        latest_by_section.setdefault(section, item)

    required_sections = ["listening", "reading", "writing", "speaking"]
    section_cards = []
    for section in required_sections:
        latest = latest_by_section.get(section)
        section_cards.append(
            {
                "section": section,
                "attempts": attempts_by_section.get(section, 0),
                "latest": latest,
                "is_completed": latest is not None and (latest.get("band") is not None or section == "writing"),
            }
        )

    bands_for_overall = [card["latest"]["band"] for card in section_cards if card.get("latest") and card["latest"].get("band") is not None]
    overall_band = calculate_overall_band(bands_for_overall)

    missing = [card["section"] for card in section_cards if not card["latest"]]
    if missing:
        recommendation = f"Keyingi qadam: {missing[0].capitalize()} bo'limini topshiring."
    elif bands_for_overall:
        weakest = min(
            (card for card in section_cards if card.get("latest") and card["latest"].get("band") is not None),
            key=lambda c: c["latest"]["band"],
        )
        recommendation = f"Eng past natija: {weakest['section'].capitalize()}. Shu bo'limni qayta mashq qiling."
    else:
        recommendation = "Test-bankdan birinchi mock testni boshlang."

    return {
        "profile": {
            "email": clean_email,
            "full_name": user["full_name"] if user else None,
            "username": user["username"] if user else None,
            "created_at": user["created_at"].isoformat() if user and user["created_at"] else None,
            "email_verified": bool(user["email_verified"]) if user else False,
            "referral_count": user["referral_count"] if user else 0,
        },
        "overview": {
            "overall_band": overall_band,
            "total_attempts": len(history),
            "completed_sections": sum(1 for card in section_cards if card["latest"]),
            "recommendation": recommendation,
        },
        "sections": section_cards,
        "history": history[:30],
    }
