from fastapi import FastAPI, HTTPException
from feature_routes import router as feature_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import httpx
import asyncio
import io
import base64
import csv
try:
    import openpyxl
except ImportError:
    openpyxl = None
from datetime import datetime
from fpdf import FPDF

from db import get_pool
from scoring import get_band_score
from auth import router as auth_router, ensure_users_table

app = FastAPI(title="IELTS Mock Exam")
app.include_router(feature_router)
app.include_router(auth_router)


@app.middleware("http")
async def no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")

# Email konfiguratsiyasi (Resend)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@ielts.sultanov.space")


@app.on_event("startup")
async def startup():
    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exam_results (
                id SERIAL PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                section TEXT NOT NULL,
                score INTEGER,
                total INTEGER,
                answers JSONB,
                writing_task1 TEXT,
                writing_task2 TEXT,
                duration_seconds INTEGER,
                submitted_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_band NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_feedback TEXT")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exam_sessions (
                id SERIAL PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT NOW(),
                sections_completed TEXT[] DEFAULT ARRAY[]::TEXT[]
            )
        """)

    await ensure_users_table()


# ─── Models ───────────────────────────────────────────────────────────────────

class StartSession(BaseModel):
    full_name: str
    email: str


class SubmitResult(BaseModel):
    session_id: int
    full_name: str
    email: str
    section: str
    score: Optional[int] = None
    total: Optional[int] = None
    answers: Optional[dict] = None
    writing_task1: Optional[str] = None
    writing_task2: Optional[str] = None
    duration_seconds: Optional[int] = None


class GradeWriting(BaseModel):
    result_id: int
    band: float
    feedback: Optional[str] = None
    send_email: bool = True


# ─── Email yuborish (Resend API) ───────────────────────────────────────────────

async def send_email(to_email: str, to_name: str, subject: str, html_body: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY sozlanmagan")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": f"IELTS Mock <{EMAIL_FROM}>",
                "to": [to_email],
                "subject": subject,
                "html": html_body
            }
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Resend xatosi: {response.text}")

async def send_email_with_attachment(to_email: str, to_name: str, subject: str, html_body: str, attachment_bytes: bytes, attachment_filename: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY sozlanmagan")

    b64_content = base64.b64encode(attachment_bytes).decode('utf-8')

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": f"IELTS Mock <{EMAIL_FROM}>",
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "attachments": [
                    {
                        "filename": attachment_filename,
                        "content": b64_content
                    }
                ]
            }
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Resend xatosi: {response.text}")


def build_result_email(name: str, section: str, score, total, band, feedback=None) -> str:
    section_names = {"listening": "Listening", "reading": "Reading", "writing": "Writing"}
    section_name = section_names.get(section, section)

    if section == "writing":
        body = f"""
        <p><b>Band Score:</b> {band if band is not None else 'Hali baholanmagan'}</p>
        {f'<p><b>Izoh:</b> {feedback}</p>' if feedback else ''}
        """
    else:
        body = f"""
        <p><b>Natija:</b> {score} / {total} to'g'ri</p>
        <p><b>Band Score:</b> {band}</p>
        """

    return f"""
    <div style="font-family:Arial,sans-serif; max-width:520px; margin:0 auto; padding:24px; border:1px solid #c9d8ff; border-radius:12px;">
      <h2 style="color:#1a56e8;">IELTS Mock — {section_name} natijasi</h2>
      <p>Assalomu alaykum, {name}!</p>
      {body}
      <p style="margin-top:20px; color:#4a5978; font-size:13px;">
        Batafsil: <a href="https://ielts.sultanov.space" style="color:#1a56e8;">ielts.sultanov.space</a>
      </p>
      <p style="color:#4a5978; font-size:12px; margin-top:16px;"> © 2026-2027 Bo'stonliq tuman ixtisoslashtirilgan maktabi</p>
    </div>
    """


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/start")
async def start_session(data: StartSession):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO exam_sessions (full_name, email)
            VALUES ($1, $2)
            RETURNING id, started_at
            """,
            data.full_name.strip(),
            data.email.strip().lower()
        )
    return {
        "session_id": row["id"],
        "message": "Imtihon boshlandi",
        "started_at": row["started_at"].isoformat()
    }


@app.post("/api/submit")
async def submit_result(data: SubmitResult):
    db = await get_pool()
    async with db.acquire() as conn:
        result_row = await conn.fetchrow(
            """
            INSERT INTO exam_results 
                (full_name, email, section, score, total, answers, 
                 writing_task1, writing_task2, duration_seconds)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            data.full_name,
            data.email,
            data.section,
            data.score,
            data.total,
            json.dumps(data.answers) if data.answers else None,
            data.writing_task1,
            data.writing_task2,
            data.duration_seconds
        )

        await conn.execute(
            """
            UPDATE exam_sessions
            SET sections_completed = array_append(sections_completed, $1)
            WHERE id = $2
            """,
            data.section,
            data.session_id
        )

        user_row = await conn.fetchrow("SELECT telegram_chat_id FROM users WHERE email = $1", data.email.lower())
        chat_id = user_row["telegram_chat_id"] if user_row else None

    percentage = round((data.score / data.total) * 100) if data.score and data.total else None
    band = get_band_score(data.score, data.total, data.section) if data.score and data.total else None

    try:
        from telegram import notify_admin_new_result
        await notify_admin_new_result(data.full_name, data.email, data.section, data.score, data.total, band)
    except Exception:
        pass

    if data.section != "writing":
        try:
            db = await get_pool()
            async with db.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (section) full_name, section, score, total, writing_band
                    FROM exam_results
                    WHERE email = $1
                    ORDER BY section, submitted_at DESC
                    """,
                    data.email.lower()
                )
            
            sections = []
            for r in rows:
                if r["section"] == "writing":
                    r_band = float(r["writing_band"]) if r["writing_band"] is not None else None
                else:
                    r_band = get_band_score(r["score"], r["total"], r["section"])
                sections.append({
                    "section": r["section"],
                    "score": r["score"],
                    "total": r["total"],
                    "band": r_band
                })
            
            pdf_bytes = build_result_pdf(data.full_name, data.email.lower(), sections)
            html = build_result_email(data.full_name, data.section, data.score, data.total, band)
            
            await send_email_with_attachment(
                data.email, data.full_name, f"IELTS Mock — {data.section.capitalize()} natijangiz",
                html, pdf_bytes, f"ielts_natija_{data.email.lower().split('@')[0]}.pdf"
            )
            
            async with db.acquire() as conn:
                await conn.execute("UPDATE exam_results SET notified = TRUE WHERE id = $1", result_row["id"])
                
            if chat_id:
                from telegram import notify_user_result_ready
                await notify_user_result_ready(chat_id, data.full_name, data.section, band, data.score, data.total)
                
        except Exception as e:
            print("Email/PDF jo'natishda xatolik:", e)

    return {
        "result_id": result_row["id"],
        "message": "Natija saqlandi",
        "section": data.section,
        "score": data.score,
        "total": data.total,
        "percentage": percentage,
        "band": band
    }


@app.get("/api/results/{email}")
async def get_results(email: str):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT section, score, total, submitted_at
            FROM exam_results
            WHERE email = $1
            ORDER BY submitted_at DESC
            LIMIT 20
            """,
            email.lower()
        )
    results = [
        {
            "section": r["section"],
            "score": r["score"],
            "total": r["total"],
            "band": get_band_score(r["score"], r["total"], r["section"]),
            "submitted_at": r["submitted_at"].isoformat()
        }
        for r in rows
    ]
    return {"email": email, "results": results}


# ─── PDF Sertifikat ─────────────────────────────────────────────────────────────

def build_result_pdf(full_name: str, email: str, sections: list) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)

    # Header
    pdf.set_fill_color(26, 86, 232)
    pdf.rect(0, 0, 210, 30, style="F")
    pdf.set_xy(0, 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(210, 8, "IELTS Mock Exam - Natija", align="C")
    pdf.set_xy(0, 18)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(210, 6, "ielts.sultanov.space", align="C")

    # Demo disclaimer banner
    pdf.set_xy(10, 36)
    pdf.set_fill_color(255, 251, 235)
    pdf.set_draw_color(217, 119, 6)
    pdf.rect(10, 36, 190, 12, style="DF")
    pdf.set_xy(12, 39)
    pdf.set_text_color(217, 119, 6)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(186, 5,
        "Diqqat: bu DEMO test rejimi natijasi. Savollar namunaviy, haqiqiy imtihon "
        "formatidagi savollar hali qo'shilmagan. Rasmiy sertifikat emas.")

    # Candidate info
    y = 56
    pdf.set_text_color(11, 23, 51)
    pdf.set_xy(10, y)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(95, 7, "Ism-familiya:")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(95, 7, full_name)
    pdf.set_xy(10, y + 7)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(95, 7, "Email:")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(95, 7, email)
    pdf.set_xy(10, y + 14)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(95, 7, "Sana:")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(95, 7, datetime.now().strftime("%d.%m.%Y"))

    # Overall band (average of graded sections)
    graded_bands = [s["band"] for s in sections if s.get("band") is not None]
    overall = round(sum(graded_bands) / len(graded_bands), 1) if graded_bands else None

    y = 84
    pdf.set_fill_color(238, 243, 255)
    pdf.rect(10, y, 190, 26, style="F")
    pdf.set_xy(10, y + 4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(74, 89, 120)
    pdf.cell(190, 6, "UMUMIY BAND SCORE", align="C")
    pdf.set_xy(10, y + 10)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(26, 86, 232)
    pdf.cell(190, 12, str(overall) if overall is not None else "Kutilmoqda", align="C")

    # Section breakdown table
    y = 118
    pdf.set_text_color(11, 23, 51)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(10, y)
    pdf.cell(190, 8, "Bo'limlar bo'yicha natija")

    y += 10
    section_labels = {"listening": "Listening", "reading": "Reading", "writing": "Writing"}
    pdf.set_fill_color(26, 86, 232)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(10, y)
    pdf.cell(63, 9, "Bo'lim", border=1, fill=True, align="C")
    pdf.cell(63, 9, "Natija", border=1, fill=True, align="C")
    pdf.cell(64, 9, "Band", border=1, fill=True, align="C")

    pdf.set_text_color(11, 23, 51)
    pdf.set_font("Helvetica", "", 10)
    for s in sections:
        y += 9
        pdf.set_xy(10, y)
        label = section_labels.get(s["section"], s["section"])
        if s["section"] == "writing":
            detail = "Tekshirilmoqda" if s.get("band") is None else "Admin tomonidan baholandi"
        else:
            detail = f'{s["score"]}/{s["total"]}' if s.get("score") is not None else "-"
        band_txt = str(s["band"]) if s.get("band") is not None else "-"
        pdf.cell(63, 9, label, border=1, align="C")
        pdf.cell(63, 9, detail, border=1, align="C")
        pdf.cell(64, 9, band_txt, border=1, align="C")

    # Footer
    pdf.set_xy(10, 275)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(74, 89, 120)
    pdf.cell(190, 5, "Bu hujjat avtomatik generatsiya qilingan - ielts.sultanov.space", align="C")

    return bytes(pdf.output())


@app.get("/api/results/{email}/pdf")
async def get_results_pdf(email: str):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (section) full_name, section, score, total, writing_band
            FROM exam_results
            WHERE email = $1
            ORDER BY section, submitted_at DESC
            """,
            email.lower()
        )

    if not rows:
        raise HTTPException(status_code=404, detail="Natija topilmadi")

    full_name = rows[0]["full_name"]
    sections = []
    for r in rows:
        if r["section"] == "writing":
            band = float(r["writing_band"]) if r["writing_band"] is not None else None
        else:
            band = get_band_score(r["score"], r["total"], r["section"])
        sections.append({
            "section": r["section"],
            "score": r["score"],
            "total": r["total"],
            "band": band
        })

    order = {"listening": 0, "reading": 1, "writing": 2}
    sections.sort(key=lambda s: order.get(s["section"], 99))

    pdf_bytes = build_result_pdf(full_name, email.lower(), sections)
    filename = f"ielts_natija_{email.lower().split('@')[0]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ─── ADMIN ENDPOINTS ────────────────────────────────────────────────────────────

def check_admin(secret: str):
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Ruxsat yo'q — noto'g'ri parol")


@app.get("/api/admin/results")
async def admin_results(secret: str = ""):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, full_name, email, section, score, total,
                   writing_band, writing_feedback, notified, submitted_at
            FROM exam_results
            ORDER BY submitted_at DESC
            LIMIT 300
            """
        )
    return {
        "total": len(rows),
        "results": [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "email": r["email"],
                "section": r["section"],
                "score": r["score"],
                "total": r["total"],
                "band": (
                    float(r["writing_band"]) if r["section"] == "writing" and r["writing_band"] is not None
                    else get_band_score(r["score"], r["total"], r["section"]) if r["section"] != "writing"
                    else None
                ),
                "writing_feedback": r["writing_feedback"],
                "notified": r["notified"],
                "submitted_at": r["submitted_at"].isoformat()
            }
            for r in rows
        ]
    }


@app.get("/api/admin/users")
async def admin_users(secret: str = "", search: Optional[str] = None):
    check_admin(secret)
    db = await get_pool()
    search_term = f"%{search.strip()}%" if search and search.strip() else None
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.created_at,
                   u.email_verified, u.is_suspended,
                   (u.avatar_mime IS NOT NULL) AS has_avatar,
                   COUNT(er.id) AS attempts
            FROM users u
            LEFT JOIN exam_results er ON er.email = u.email
            WHERE ($1::text IS NULL OR u.username ILIKE $1 OR u.email ILIKE $1 OR u.full_name ILIKE $1)
            GROUP BY u.id
            ORDER BY u.created_at DESC
            LIMIT 500
            """,
            search_term
        )
    return {
        "total": len(rows),
        "users": [
            {
                "id": r["id"],
                "username": r["username"],
                "email": r["email"],
                "full_name": r["full_name"],
                "created_at": r["created_at"].isoformat(),
                "email_verified": r["email_verified"],
                "is_suspended": r["is_suspended"],
                "attempts": r["attempts"],
                "avatar_url": f"/api/auth/avatar/{r['username']}" if r.get("has_avatar") else None
            }
            for r in rows
        ]
    }


@app.post("/api/admin/users/{user_id}/suspend")
async def admin_suspend_user(user_id: int, secret: str = ""):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET is_suspended = NOT is_suspended WHERE id = $1 RETURNING id, is_suspended",
            user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    return {
        "message": "Foydalanuvchi bloklandi" if row["is_suspended"] else "Foydalanuvchi blokdan chiqarildi",
        "is_suspended": row["is_suspended"]
    }


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, secret: str = ""):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM users WHERE id = $1 RETURNING id", user_id)
        if not row:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    return {"message": "Foydalanuvchi o'chirildi"}


@app.post("/api/admin/grade-writing")
async def grade_writing(data: GradeWriting, secret: str = ""):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE exam_results
            SET writing_band = $1, writing_feedback = $2
            WHERE id = $3
            RETURNING full_name, email, section
            """,
            data.band, data.feedback, data.result_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Natija topilmadi")

    if data.send_email:
        try:
            html = build_result_email(row["full_name"], "writing", None, None, data.band, data.feedback)
            
            async with db.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (section) full_name, section, score, total, writing_band
                    FROM exam_results
                    WHERE email = $1
                    ORDER BY section, submitted_at DESC
                    """,
                    row["email"].lower()
                )
            sections = []
            for r in rows:
                if r["section"] == "writing":
                    r_band = float(r["writing_band"]) if r["writing_band"] is not None else None
                else:
                    r_band = get_band_score(r["score"], r["total"], r["section"])
                sections.append({
                    "section": r["section"],
                    "score": r["score"],
                    "total": r["total"],
                    "band": r_band
                })
                
            pdf_bytes = build_result_pdf(row["full_name"], row["email"].lower(), sections)
            await send_email_with_attachment(
                row["email"], row["full_name"], "IELTS Mock — Writing natijangiz tayyor", 
                html, pdf_bytes, f"ielts_natija_{row['email'].lower().split('@')[0]}.pdf"
            )
            
            async with db.acquire() as conn:
                await conn.execute("UPDATE exam_results SET notified = TRUE WHERE id = $1", data.result_id)
                user_row = await conn.fetchrow("SELECT telegram_chat_id FROM users WHERE email = $1", row["email"].lower())
                
            if user_row and user_row["telegram_chat_id"]:
                from telegram import notify_user_writing_graded
                await notify_user_writing_graded(user_row["telegram_chat_id"], row["full_name"], data.band, data.feedback)
                
        except Exception as e:
            return {"message": "Baho saqlandi, lekin email yuborilmadi", "error": str(e)}

    return {"message": "Baho saqlandi va email yuborildi"}


@app.post("/api/admin/notify/{result_id}")
async def notify_result(result_id: int, secret: str = ""):
    """Listening/Reading natijasini emailga yuborish"""
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT full_name, email, section, score, total FROM exam_results WHERE id = $1",
            result_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Natija topilmadi")

        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (section) full_name, section, score, total, writing_band
            FROM exam_results
            WHERE email = $1
            ORDER BY section, submitted_at DESC
            """,
            row["email"].lower()
        )
        sections = []
        for r in rows:
            if r["section"] == "writing":
                r_band = float(r["writing_band"]) if r["writing_band"] is not None else None
            else:
                r_band = get_band_score(r["score"], r["total"], r["section"])
            sections.append({
                "section": r["section"],
                "score": r["score"],
                "total": r["total"],
                "band": r_band
            })

    band = get_band_score(row["score"], row["total"], row["section"])
    try:
        pdf_bytes = build_result_pdf(row["full_name"], row["email"].lower(), sections)
        html = build_result_email(row["full_name"], row["section"], row["score"], row["total"], band)
        
        await send_email_with_attachment(
            row["email"], row["full_name"], f"IELTS Mock — {row['section'].capitalize()} natijangiz", 
            html, pdf_bytes, f"ielts_natija_{row['email'].lower().split('@')[0]}.pdf"
        )
        
        async with db.acquire() as conn:
            await conn.execute("UPDATE exam_results SET notified = TRUE WHERE id = $1", result_id)
            user_row = await conn.fetchrow("SELECT telegram_chat_id FROM users WHERE email = $1", row["email"].lower())
            
        if user_row and user_row["telegram_chat_id"]:
            from telegram import notify_user_result_ready
            await notify_user_result_ready(user_row["telegram_chat_id"], row["full_name"], row["section"], band, row["score"], row["total"])
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email yuborilmadi: {str(e)}")

    return {"message": "Email yuborildi"}


@app.get("/api/admin/stats")
async def admin_stats(secret: str = "", days: int = 30):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        verified_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE email_verified = TRUE")
        total_results = await conn.fetchval("SELECT COUNT(*) FROM exam_results")
        active_today = await conn.fetchval("SELECT COUNT(DISTINCT email) FROM exam_results WHERE DATE(submitted_at) = CURRENT_DATE")
        
        daily_reg_rows = await conn.fetch(
            """
            SELECT DATE(created_at) as date, COUNT(*) as count 
            FROM users 
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at) 
            ORDER BY date
            """
        )
        
        daily_act_rows = await conn.fetch(
            """
            SELECT DATE(submitted_at) as date, COUNT(DISTINCT email) as count 
            FROM exam_results 
            WHERE submitted_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(submitted_at) 
            ORDER BY date
            """
        )

        sec_rows = await conn.fetch("SELECT section, COUNT(*) as count FROM exam_results GROUP BY section")
        all_res = await conn.fetch("SELECT section, score, total, writing_band FROM exam_results")
    
    overview = {
        "total_users": total_users,
        "verified_users": verified_users,
        "total_results": total_results,
        "active_today": active_today
    }
    
    daily_registrations = [{"date": str(r["date"]), "count": r["count"]} for r in daily_reg_rows]
    daily_active_users = [{"date": str(r["date"]), "count": r["count"]} for r in daily_act_rows]
    section_attempts = {r["section"]: r["count"] for r in sec_rows}
    
    band_distribution = {"listening": {}, "reading": {}, "writing": {}}
    for r in all_res:
        s = r["section"]
        if s == "writing":
            b = float(r["writing_band"]) if r["writing_band"] is not None else None
        else:
            b = get_band_score(r["score"], r["total"], s)
            
        if b is not None:
            b_str = str(b)
            band_distribution[s][b_str] = band_distribution[s].get(b_str, 0) + 1

    return {
        "overview": overview,
        "daily_registrations": daily_registrations,
        "daily_active_users": daily_active_users,
        "section_attempts": section_attempts,
        "band_distribution": band_distribution
    }


@app.get("/api/admin/export/results")
async def export_results(secret: str = "", format: str = "csv"):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, full_name, email, section, score, total,
                   writing_band, submitted_at
            FROM exam_results
            ORDER BY submitted_at DESC
            """
        )
        
    data = []
    for r in rows:
        b = float(r["writing_band"]) if r["section"] == "writing" and r["writing_band"] is not None else get_band_score(r["score"], r["total"], r["section"]) if r["section"] != "writing" else ""
        data.append({
            "ID": r["id"],
            "Ism": r["full_name"],
            "Email": r["email"],
            "Bo'lim": r["section"],
            "Natija": f"{r['score']}/{r['total']}" if r["score"] is not None else "",
            "Band": b,
            "Sana": r["submitted_at"].isoformat()
        })
        
    if format == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        output.seek(0)
        return StreamingResponse(
            iter([b'\xef\xbb\xbf' + output.getvalue().encode('utf-8')]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=results.csv"}
        )
    elif format == "excel":
        if not openpyxl:
            raise HTTPException(status_code=500, detail="openpyxl o'rnatilmagan")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Results"
        if data:
            ws.append(list(data[0].keys()))
            for d in data:
                ws.append(list(d.values()))
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=results.xlsx"}
        )

@app.get("/api/admin/export/users")
async def export_users(secret: str = "", format: str = "csv"):
    check_admin(secret)
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.created_at,
                   u.email_verified, u.is_suspended, u.telegram_chat_id, u.referral_count,
                   COUNT(er.id) AS attempts
            FROM users u
            LEFT JOIN exam_results er ON er.email = u.email
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        )
        
    data = []
    for r in rows:
        data.append({
            "ID": r["id"],
            "Username": r["username"],
            "Email": r["email"],
            "To'liq Ism": r["full_name"],
            "Sana": r["created_at"].isoformat(),
            "Tasdiqlangan": "Ha" if r["email_verified"] else "Yo'q",
            "Bloklangan": "Ha" if r["is_suspended"] else "Yo'q",
            "Urinishlar": r["attempts"],
            "Telegram": r["telegram_chat_id"] or "",
            "Taklif qildi": r["referral_count"] or 0
        })
        
    if format == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        output.seek(0)
        return StreamingResponse(
            iter([b'\xef\xbb\xbf' + output.getvalue().encode('utf-8')]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users.csv"}
        )
    elif format == "excel":
        if not openpyxl:
            raise HTTPException(status_code=500, detail="openpyxl o'rnatilmagan")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Users"
        if data:
            ws.append(list(data[0].keys()))
            for d in data:
                ws.append(list(d.values()))
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=users.xlsx"}
        )


# ─── Static Files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/admin")
async def admin_page():
    return FileResponse("static/admin.html")

@app.get("/profile")
async def profile_page():
    return FileResponse("static/profile.html")

@app.get("/listening-demo")
async def listening_demo_page():
    return FileResponse("static/Listening-demo.html")

@app.get("/reading-demo")
async def reading_demo_page():
    return FileResponse("static/Reading-demo.html")

@app.get("/writing-demo")
async def writing_demo_page():
    return FileResponse("static/writing-demo.html")

@app.get("/{path:path}")
async def catch_all(path: str):
    return FileResponse("static/index.html")
