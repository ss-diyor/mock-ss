from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File, Form
from feature_routes import router as feature_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import httpx
import asyncio
import io
import base64
import csv
import hmac
try:
    import openpyxl
except ImportError:
    openpyxl = None
from datetime import datetime
from fpdf import FPDF

from db import get_pool
from scoring import get_band_score
from auth import router as auth_router, ensure_users_table, get_current_user
from groups_db import ensure_center_group_tables, DEFAULT_MAX_GROUPS_PER_CENTER, DEFAULT_MAX_STUDENTS_PER_CENTER
from head_teacher_routes import router as head_teacher_router
from teacher_routes import router as teacher_router
from school_routes import router as school_router
from school_staff_routes import router as school_staff_router
from billing_routes import router as billing_router
from test_catalog_routes import router as test_catalog_router
from branding import ORGANIZATION_TYPES, branding_payload

app = FastAPI(title="IELTS Mock SS")
app.include_router(feature_router)
app.include_router(auth_router)
app.include_router(head_teacher_router)
app.include_router(teacher_router)
app.include_router(school_router)
app.include_router(school_staff_router)
app.include_router(billing_router)
app.include_router(test_catalog_router)


@app.middleware("http")
async def no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

ADMIN_SECRET = os.environ.get("ADMIN_SECRET")
MIN_ADMIN_SECRET_LENGTH = 32

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
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_task_achievement NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_coherence_cohesion NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_lexical_resource NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_grammar_accuracy NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_graded_at TIMESTAMP")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS grader_name TEXT")
        # Speaking bo'limi uchun ustunlar
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_telegram_file_id TEXT")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_band NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_feedback TEXT")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_graded_at TIMESTAMP")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_fluency_coherence NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_lexical_resource NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_grammar_accuracy NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS speaking_pronunciation NUMERIC")


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
    await ensure_center_group_tables()
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_templates (
                id SERIAL PRIMARY KEY,
                teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                section TEXT NOT NULL CHECK(section IN ('writing','speaking')),
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS resubmission_requests (
                id SERIAL PRIMARY KEY,
                result_id INTEGER NOT NULL REFERENCES exam_results(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                section TEXT NOT NULL CHECK(section IN ('writing','speaking')),
                reason TEXT NOT NULL,
                due_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                replacement_result_id INTEGER REFERENCES exam_results(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(result_id, status)
            )
        """)
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS test_id INTEGER REFERENCES tests(id) ON DELETE SET NULL")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS test_slug TEXT")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS test_mode TEXT")


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
    test_id: Optional[int] = None
    test_slug: Optional[str] = None
    test_mode: Optional[str] = None


class GradeWriting(BaseModel):
    result_id: int
    band: float
    feedback: Optional[str] = None
    send_email: bool = True
    task_achievement: Optional[float] = None
    coherence_cohesion: Optional[float] = None
    lexical_resource: Optional[float] = None
    grammar_accuracy: Optional[float] = None


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

    if section == "writing" or score is None or total is None:
        body = f"""
        <p><b>Holat:</b> {'Baholandi' if band is not None else 'Qabul qilindi, baholash kutilmoqda'}</p>
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
async def start_session(data: StartSession, current_user: dict = Depends(get_current_user)):
    if not current_user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Emailni tasdiqlash talab qilinadi")

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO exam_sessions (full_name, email)
            VALUES ($1, $2)
            RETURNING id, started_at
            """,
            current_user["full_name"].strip(),
            current_user["email"].strip().lower()
        )
    return {
        "session_id": row["id"],
        "message": "Imtihon boshlandi",
        "started_at": row["started_at"].isoformat()
    }


@app.post("/api/submit")
async def submit_result(data: SubmitResult, current_user: dict = Depends(get_current_user)):
    if not current_user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Emailni tasdiqlash talab qilinadi")
    if data.section not in {"listening", "reading", "writing", "speaking"}:
        raise HTTPException(status_code=400, detail="Bo'lim noto'g'ri")
    if data.score is not None and data.total is not None:
        if data.total <= 0 or data.score < 0 or data.score > data.total:
            raise HTTPException(status_code=400, detail="Natija qiymatlari noto'g'ri")
    if data.section in {"listening", "reading"} and (data.score is None or data.total is None):
        raise HTTPException(status_code=400, detail="Listening/Reading uchun score va total majburiy")
    if data.answers and len(json.dumps(data.answers)) > 1_000_000:
        raise HTTPException(status_code=413, detail="Javoblar hajmi juda katta")

    writing_task1, writing_task2 = data.writing_task1, data.writing_task2
    if data.section == "writing" and data.answers:
        # Turli HTML test konstruktorlari matnni turli kalitlarda yuborishi mumkin.
        writing_task1 = writing_task1 or next((data.answers.get(key) for key in (
            "writing_task1", "task1", "task_1", "part1", "part_1", "answer1"
        ) if data.answers.get(key)), None)
        writing_task2 = writing_task2 or next((data.answers.get(key) for key in (
            "writing_task2", "task2", "task_2", "part2", "part_2", "answer2", "essay"
        ) if data.answers.get(key)), None)
    writing_task1 = str(writing_task1).strip() if writing_task1 is not None else None
    writing_task2 = str(writing_task2).strip() if writing_task2 is not None else None
    if writing_task1 and len(writing_task1) > 100_000:
        raise HTTPException(status_code=413, detail="Writing Task 1 matni juda katta")
    if writing_task2 and len(writing_task2) > 100_000:
        raise HTTPException(status_code=413, detail="Writing Task 2 matni juda katta")

    user_email = current_user["email"].strip().lower()
    user_name = current_user["full_name"].strip()

    db = await get_pool()
    async with db.acquire() as conn:
        if data.test_id and not await conn.fetchval(
            "SELECT 1 FROM tests WHERE id=$1 AND status='published'", data.test_id
        ):
            raise HTTPException(status_code=404, detail="Test topilmadi yoki publish qilinmagan")
        session_row = await conn.fetchrow(
            "SELECT id FROM exam_sessions WHERE id = $1 AND email = $2",
            data.session_id, user_email
        )
        if not session_row:
            raise HTTPException(status_code=403, detail="Bu sessiyaga ruxsat yo'q")

        result_row = await conn.fetchrow(
            """
            INSERT INTO exam_results 
                (full_name, email, section, score, total, answers, 
                 writing_task1, writing_task2, duration_seconds, test_id, test_slug, test_mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            """,
            user_name,
            user_email,
            data.section,
            data.score,
            data.total,
            json.dumps(data.answers) if data.answers else None,
            writing_task1,
            writing_task2,
            data.duration_seconds,
            data.test_id,
            (data.test_slug or "")[:120] or None,
            data.test_mode if data.test_mode in {"full", "practice"} else None
        )

        await conn.execute(
            """
            UPDATE exam_sessions
            SET sections_completed = array_append(sections_completed, $1)
            WHERE id = $2
              AND NOT ($1 = ANY(sections_completed))
            """,
            data.section,
            data.session_id
        )

        user_row = await conn.fetchrow("SELECT telegram_chat_id FROM users WHERE email = $1", user_email)
        chat_id = user_row["telegram_chat_id"] if user_row else None

    percentage = round((data.score / data.total) * 100) if data.score is not None and data.total else None
    band = get_band_score(data.score, data.total, data.section) if data.score is not None and data.total else None

    try:
        from telegram import notify_admin_new_result
        await notify_admin_new_result(user_name, user_email, data.section, data.score, data.total, band)
    except Exception:
        pass

    if current_user.get("group_id"):
        try:
            from notifications import notify_teacher_new_result
            db3 = await get_pool()
            async with db3.acquire() as conn3:
                await notify_teacher_new_result(conn3, current_user["group_id"], user_name, data.section, band)
        except Exception:
            pass

    if current_user.get("center_id"):
        try:
            from notifications import notify_head_teacher_new_result
            db4 = await get_pool()
            async with db4.acquire() as conn4:
                await notify_head_teacher_new_result(conn4, current_user["center_id"], user_name, data.section, band)
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
                    user_email
                )
            
            sections = []
            for r in rows:
                if r["section"] == "writing":
                    r_band = float(r["writing_band"]) if r["writing_band"] is not None else None
                elif r["score"] is not None and r["total"] is not None:
                    r_band = get_band_score(r["score"], r["total"], r["section"])
                else:
                    r_band = None
                sections.append({
                    "section": r["section"],
                    "score": r["score"],
                    "total": r["total"],
                    "band": r_band
                })
            
            pdf_bytes = build_result_pdf(user_name, user_email, sections)
            html = build_result_email(user_name, data.section, data.score, data.total, band)
            
            await send_email_with_attachment(
                user_email, user_name, f"IELTS Mock — {data.section.capitalize()} natijangiz",
                html, pdf_bytes, f"ielts_natija_{user_email.split('@')[0]}.pdf"
            )
            
            async with db.acquire() as conn:
                await conn.execute("UPDATE exam_results SET notified = TRUE WHERE id = $1", result_row["id"])
                
            if chat_id:
                from telegram import notify_user_result_ready
                await notify_user_result_ready(chat_id, user_name, data.section, band, data.score, data.total)
                
        except Exception as e:
            print("Email/PDF jo'natishda xatolik:", e)
    else:
        try:
            await send_email(
                user_email, user_name, "IELTS Mock — Writing javobingiz qabul qilindi",
                build_result_email(user_name, "writing", None, None, None)
            )
            if chat_id:
                from telegram import notify_user_result_ready
                await notify_user_result_ready(chat_id, user_name, "writing", None, None, None)
        except Exception as e:
            print("Writing xabarnomasida xatolik:", e)

    return {
        "result_id": result_row["id"],
        "message": "Natija saqlandi",
        "section": data.section,
        "score": data.score,
        "total": data.total,
        "percentage": percentage,
        "band": band
    }


@app.post("/api/submit-speaking")
async def submit_speaking(
    session_id: int = Form(...),
    audio: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """O'quvchi speaking audiosini yuboradi — Telegram guruhiga saqlanadi."""
    if not current_user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Emailni tasdiqlash talab qilinadi")

    # Fayl hajmini tekshirish: max 25MB (Telegram limiti)
    MAX_SIZE = 25 * 1024 * 1024
    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="Audio fayli 25MB dan katta bo'lmasin")
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio fayli bo'sh")

    user_email = current_user["email"].strip().lower()
    user_name = current_user["full_name"].strip()

    db = await get_pool()
    async with db.acquire() as conn:
        session_row = await conn.fetchrow(
            "SELECT id FROM exam_sessions WHERE id = $1 AND email = $2",
            session_id, user_email
        )
        if not session_row:
            raise HTTPException(status_code=403, detail="Bu sessiyaga ruxsat yo'q")

        # Avval DB ga yozib olamiz (file_id keyinroq yangilanadi)
        result_row = await conn.fetchrow(
            """
            INSERT INTO exam_results
                (full_name, email, section, duration_seconds)
            VALUES ($1, $2, 'speaking', NULL)
            RETURNING id
            """,
            user_name, user_email
        )
        result_id = result_row["id"]

        await conn.execute(
            """
            UPDATE exam_sessions
            SET sections_completed = array_append(sections_completed, 'speaking')
            WHERE id = $1
              AND NOT ('speaking' = ANY(sections_completed))
            """,
            session_id
        )

    # Telegram guruhiga audio yuborish
    from telegram import send_voice_to_telegram, notify_admin_new_speaking, TELEGRAM_SPEAKING_CHAT_ID
    caption = (
        f"🎤 <b>Speaking Audio</b>\n\n"
        f"👤 {user_name}\n"
        f"📧 {user_email}\n"
        f"🆔 result_id: {result_id}"
    )
    filename = f"speaking_{result_id}_{user_email.split('@')[0]}.ogg"
    file_id = await send_voice_to_telegram(audio_bytes, filename, caption)

    if file_id:
        db = await get_pool()
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE exam_results SET speaking_telegram_file_id = $1 WHERE id = $2",
                file_id, result_id
            )
    else:
        # file_id olmasa ham natija saqlanadi, log qilamiz
        print(f"[Speaking] Telegram yuborishda xatolik. result_id={result_id}")

    # Admin ga xabar
    try:
        await notify_admin_new_speaking(user_name, user_email, file_id)
    except Exception:
        pass

    # Teacher ga xabar
    if current_user.get("group_id"):
        try:
            from notifications import notify_teacher_new_result
            db2 = await get_pool()
            async with db2.acquire() as conn2:
                await notify_teacher_new_result(conn2, current_user["group_id"], user_name, "speaking", None)
        except Exception:
            pass

    return {
        "result_id": result_id,
        "message": "Speaking audioniz muvaffaqiyatli yuborildi",
        "telegram_saved": file_id is not None
    }

@app.get("/api/results")
async def get_my_results(current_user: dict = Depends(get_current_user)):
    return await get_results(current_user["email"], current_user)


@app.get("/api/results/pdf")
async def get_my_results_pdf(current_user: dict = Depends(get_current_user)):
    return await get_results_pdf(current_user["email"], current_user)


@app.get("/api/results/{email}")
async def get_results(email: str, current_user: dict = Depends(get_current_user)):
    if email.strip().lower() != current_user["email"].strip().lower():
        raise HTTPException(status_code=403, detail="Faqat o'zingizning natijalaringizni ko'rishingiz mumkin")

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
            "band": get_band_score(r["score"], r["total"], r["section"])
                    if r["score"] is not None and r["total"] is not None else None,
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
async def get_results_pdf(email: str, current_user: dict = Depends(get_current_user)):
    if email.strip().lower() != current_user["email"].strip().lower():
        raise HTTPException(status_code=403, detail="Faqat o'zingizning natijalaringizni yuklab olishingiz mumkin")

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
        elif r["score"] is not None and r["total"] is not None:
            band = get_band_score(r["score"], r["total"], r["section"])
        else:
            band = None
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
    if not ADMIN_SECRET or ADMIN_SECRET == "admin123" or len(ADMIN_SECRET) < MIN_ADMIN_SECRET_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=f"ADMIN_SECRET sozlanmagan yoki {MIN_ADMIN_SECRET_LENGTH} belgidan qisqa"
        )
    if not hmac.compare_digest(secret, ADMIN_SECRET):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q — noto'g'ri parol")


def require_admin(x_admin_secret: str = Header("", alias="X-Admin-Secret")):
    check_admin(x_admin_secret)


@app.get("/api/admin/results")
async def admin_results(_: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT er.id, er.full_name, er.email, er.section, er.score, er.total,
                   er.writing_band, er.writing_feedback, er.notified, er.submitted_at,
                   COALESCE(t.title, er.test_slug, 'IELTS Mock SS') AS test_title
            FROM exam_results er LEFT JOIN tests t ON t.id=er.test_id
            ORDER BY er.submitted_at DESC
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
                "test_title": r["test_title"],
                "score": r["score"],
                "total": r["total"],
                "band": (
                    float(r["writing_band"]) if r["section"] == "writing" and r["writing_band"] is not None
                    else get_band_score(r["score"], r["total"], r["section"])
                    if r["section"] != "writing" and r["score"] is not None and r["total"] is not None
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
async def admin_users(search: Optional[str] = None, _: None = Depends(require_admin)):
    db = await get_pool()
    search_term = f"%{search.strip()}%" if search and search.strip() else None
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.created_at,
                   u.email_verified, u.is_suspended, u.deleted_at,
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
                "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None,
                "attempts": r["attempts"],
                "avatar_url": f"/api/auth/avatar/{r['username']}" if r.get("has_avatar") else None
            }
            for r in rows
        ]
    }


class CenterCreateIn(BaseModel):
    name: str
    organization_type: str = "learning_center"
    max_groups: Optional[int] = None
    max_students: Optional[int] = None


class AssignHeadTeacherIn(BaseModel):
    user_id: int


class AdminPaymentReviewIn(BaseModel):
    action: str
    note: Optional[str] = None


@app.post("/api/admin/centers")
async def admin_create_center(data: CenterCreateIn, _: None = Depends(require_admin)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tashkilot nomini kiriting")
    if data.organization_type not in ORGANIZATION_TYPES:
        raise HTTPException(status_code=400, detail="Tashkilot turi noto'g'ri")

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO centers (name, organization_type, max_groups, max_students, brand_name)
            VALUES ($1, $2, $3, $4, $1)
            RETURNING id, name, organization_type, max_groups, max_students, is_active, created_at
            """,
            name, data.organization_type, data.max_groups, data.max_students
        )
    return dict(row) | {"created_at": row["created_at"].isoformat()}


@app.get("/api/admin/centers")
async def admin_list_centers(_: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.name, c.organization_type, c.slug, c.brand_name, c.subscription_required, c.test_upload_enabled,
                   c.brand_primary_color, c.brand_logo_url,
                   c.is_active, c.deleted_at, c.max_groups, c.max_students, c.created_at,
                   c.owner_id, owner.full_name AS owner_name, owner.email AS owner_email,
                   sub.status AS subscription_status, sub.current_period_end,
                   COUNT(DISTINCT g.id) AS groups_count,
                   COUNT(DISTINCT u.id) FILTER (WHERE u.role = 'student') AS students_count
            FROM centers c
            LEFT JOIN users owner ON owner.id = c.owner_id
            LEFT JOIN organization_subscriptions sub ON sub.center_id = c.id
            LEFT JOIN groups g ON g.center_id = c.id
            LEFT JOIN users u ON u.center_id = c.id
            GROUP BY c.id, owner.full_name, owner.email, sub.status, sub.current_period_end
            ORDER BY c.created_at DESC
            """
        )
    return [
        {
            "id": r["id"], "name": r["name"], "organization_type": r["organization_type"],
            "slug": r["slug"], "brand_name": r["brand_name"],
            "subscription_required": r["subscription_required"],
            "test_upload_enabled": r["test_upload_enabled"],
            "subscription_status": r["subscription_status"],
            "subscription_period_end": r["current_period_end"].isoformat() if r["current_period_end"] else None,
            "primary_color": r["brand_primary_color"], "logo_url": r["brand_logo_url"],
            "is_active": r["is_active"],
            "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None,
            "max_groups": r["max_groups"] or DEFAULT_MAX_GROUPS_PER_CENTER,
            "max_students": r["max_students"] or DEFAULT_MAX_STUDENTS_PER_CENTER,
            "created_at": r["created_at"].isoformat(),
            "owner_id": r["owner_id"], "owner_name": r["owner_name"], "owner_email": r["owner_email"],
            "groups_count": r["groups_count"], "students_count": r["students_count"],
        }
        for r in rows
    ]


@app.post("/api/admin/centers/{center_id}/toggle-test-upload")
async def admin_toggle_test_upload(center_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("UPDATE centers SET test_upload_enabled=NOT test_upload_enabled WHERE id=$1 RETURNING test_upload_enabled", center_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
    return {"test_upload_enabled": row["test_upload_enabled"]}


@app.post("/api/admin/centers/{center_id}/toggle-subscription")
async def admin_toggle_subscription(center_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            center = await conn.fetchrow(
                """
                UPDATE centers SET subscription_required=NOT subscription_required
                WHERE id=$1 RETURNING id, subscription_required
                """,
                center_id
            )
            if not center:
                raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
            if center["subscription_required"]:
                await conn.execute(
                    """
                    INSERT INTO organization_subscriptions(center_id, status, trial_ends_at, updated_at)
                    VALUES ($1, 'trial', NOW() + INTERVAL '14 days', NOW())
                    ON CONFLICT(center_id) DO NOTHING
                    """,
                    center_id
                )
    return {
        "subscription_required": center["subscription_required"],
        "message": "Obuna talabi yoqildi" if center["subscription_required"] else "Obuna talabi o'chirildi",
    }


@app.get("/api/admin/subscription-payments")
async def admin_subscription_payments(status: Optional[str] = None, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pay.id, pay.order_code, pay.billing_cycle, pay.amount, pay.payer_name,
                   pay.transaction_reference, pay.status, pay.review_note, pay.created_at,
                   pay.receipt_mime, c.id AS center_id, c.name AS center_name, p.name AS plan_name
            FROM subscription_payments pay
            JOIN centers c ON c.id=pay.center_id
            JOIN subscription_plans p ON p.id=pay.plan_id
            WHERE ($1::text IS NULL OR pay.status=$1)
            ORDER BY CASE WHEN pay.status='pending' THEN 0 ELSE 1 END, pay.created_at DESC
            LIMIT 500
            """,
            status
        )

        await conn.execute(
            """
            UPDATE resubmission_requests
            SET status='submitted',replacement_result_id=$1
            WHERE student_id=$2 AND section=$3 AND status='pending'
            """,
            result_row["id"], current_user["id"], data.section
        )
    return [dict(row) | {"created_at": row["created_at"].isoformat()} for row in rows]


@app.get("/api/admin/subscription-payments/{payment_id}/receipt")
async def admin_subscription_receipt(payment_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT receipt_data, receipt_mime FROM subscription_payments WHERE id=$1", payment_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="To'lov topilmadi")
    return Response(content=bytes(row["receipt_data"]), media_type=row["receipt_mime"])


@app.post("/api/admin/subscription-payments/{payment_id}/review")
async def admin_review_subscription_payment(
    payment_id: int,
    data: AdminPaymentReviewIn,
    _: None = Depends(require_admin)
):
    if data.action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Amal noto'g'ri")
    note = data.note.strip()[:500] if data.note else None
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            payment = await conn.fetchrow(
                "SELECT * FROM subscription_payments WHERE id=$1 FOR UPDATE", payment_id
            )
            if not payment:
                raise HTTPException(status_code=404, detail="To'lov topilmadi")
            if payment["status"] != "pending":
                raise HTTPException(status_code=409, detail="To'lov oldin ko'rib chiqilgan")
            new_status = "approved" if data.action == "approve" else "rejected"
            await conn.execute(
                """
                UPDATE subscription_payments
                SET status=$1, review_note=$2, reviewed_at=NOW() WHERE id=$3
                """,
                new_status, note, payment_id
            )
            if data.action == "approve":
                await conn.execute(
                    """
                    INSERT INTO organization_subscriptions(
                        center_id, plan_id, status, current_period_start, current_period_end, grace_ends_at, updated_at
                    ) VALUES (
                        $1, $2, 'active', NOW(),
                        NOW() + CASE WHEN $3='yearly' THEN INTERVAL '1 year' ELSE INTERVAL '1 month' END,
                        NOW() + CASE WHEN $3='yearly' THEN INTERVAL '1 year 7 days' ELSE INTERVAL '1 month 7 days' END,
                        NOW()
                    )
                    ON CONFLICT(center_id) DO UPDATE SET
                        plan_id=EXCLUDED.plan_id,
                        status='active',
                        current_period_start=GREATEST(COALESCE(organization_subscriptions.current_period_end, NOW()), NOW()),
                        current_period_end=GREATEST(COALESCE(organization_subscriptions.current_period_end, NOW()), NOW())
                            + CASE WHEN $3='yearly' THEN INTERVAL '1 year' ELSE INTERVAL '1 month' END,
                        grace_ends_at=GREATEST(COALESCE(organization_subscriptions.current_period_end, NOW()), NOW())
                            + CASE WHEN $3='yearly' THEN INTERVAL '1 year 7 days' ELSE INTERVAL '1 month 7 days' END,
                        updated_at=NOW()
                    """,
                    payment["center_id"], payment["plan_id"], payment["billing_cycle"]
                )
    return {"message": "To'lov tasdiqlandi" if data.action == "approve" else "To'lov rad etildi"}


@app.get("/api/branding/{slug}")
async def public_branding(slug: str):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, organization_type, slug, brand_name, brand_primary_color,
                   brand_secondary_color, brand_logo_url, brand_favicon_url,
                   brand_contact_email, brand_contact_phone, show_powered_by
            FROM centers WHERE LOWER(slug)=LOWER($1) AND is_active=TRUE
            """,
            slug.strip()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
    return branding_payload(row)


@app.post("/api/admin/centers/{center_id}/assign-head-teacher")
async def admin_assign_head_teacher(center_id: int, data: AssignHeadTeacherIn, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            center = await conn.fetchrow("SELECT id FROM centers WHERE id=$1 FOR UPDATE", center_id)
            if not center:
                raise HTTPException(status_code=404, detail="Markaz topilmadi")

            user = await conn.fetchrow("SELECT id, role FROM users WHERE id=$1", data.user_id)
            if not user:
                raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
            if user["role"] != "student":
                raise HTTPException(
                    status_code=400,
                    detail="Faqat oddiy (student) hisob head-teacher qilib tayinlanishi mumkin"
                )

            await conn.execute(
                "UPDATE users SET role='head_teacher', center_id=$1 WHERE id=$2", center_id, data.user_id
            )
            await conn.execute("UPDATE centers SET owner_id=$1 WHERE id=$2", data.user_id, center_id)

    return {"message": "Head-teacher tayinlandi"}


@app.post("/api/admin/centers/{center_id}/deactivate")
async def admin_toggle_center(center_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE centers SET is_active = NOT is_active WHERE id=$1 RETURNING id, is_active",
            center_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Markaz topilmadi")
    return {
        "message": "Markaz yopildi" if not row["is_active"] else "Markaz qayta ochildi",
        "is_active": row["is_active"]
    }


ADMIN_ENTITY_CONFIG = {
    "group": ("groups", "name", "is_active"),
    "class": ("school_classes", "name", "is_active"),
    "staff": ("school_staff", "employee_code", "is_active"),
    "subject": ("school_subjects", "name", "is_active"),
    "position": ("school_positions", "name", None),
    "user": ("users", "full_name", "is_suspended"),
    "test": ("tests", "title", None),
}


@app.get("/api/admin/centers/{center_id}/inventory")
async def admin_center_inventory(center_id: int, _: None = Depends(require_admin)):
    """Tashkilot tarkibini bitta xavfsiz boshqaruv oynasida ko'rsatadi."""
    db = await get_pool()
    async with db.acquire() as conn:
        center = await conn.fetchrow("SELECT id, name FROM centers WHERE id=$1", center_id)
        if not center:
            raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
        groups = await conn.fetch("SELECT id,name,is_active,deleted_at FROM groups WHERE center_id=$1 ORDER BY name", center_id)
        classes = await conn.fetch("SELECT id,name,academic_year,is_active,deleted_at FROM school_classes WHERE center_id=$1 ORDER BY name", center_id)
        subjects = await conn.fetch("SELECT id,name,is_active,deleted_at FROM school_subjects WHERE center_id=$1 ORDER BY name", center_id)
        positions = await conn.fetch("SELECT id,name,deleted_at FROM school_positions WHERE center_id=$1 ORDER BY name", center_id)
        staff = await conn.fetch("""
            SELECT ss.id, COALESCE(u.full_name,ss.employee_code,'Xodim') AS name,
                   ss.is_active,ss.deleted_at
            FROM school_staff ss LEFT JOIN users u ON u.id=ss.user_id
            WHERE ss.center_id=$1 ORDER BY name
        """, center_id)
        users = await conn.fetch("SELECT id,full_name AS name,email,role,is_suspended,deleted_at FROM users WHERE center_id=$1 ORDER BY full_name", center_id)
        tests = await conn.fetch("SELECT id,title AS name,status,deleted_at FROM tests WHERE center_id=$1 ORDER BY title", center_id)

    def serialize(rows):
        return [dict(r) | {"deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None} for r in rows]
    return {"center": dict(center), "groups": serialize(groups), "classes": serialize(classes),
            "subjects": serialize(subjects), "positions": serialize(positions),
            "staff": serialize(staff), "users": serialize(users), "tests": serialize(tests)}


@app.post("/api/admin/centers/{center_id}/trash")
async def admin_trash_center(center_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("UPDATE centers SET deleted_at=NOW(),is_active=FALSE WHERE id=$1 RETURNING name", center_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
    return {"message": f"{row['name']} savatga o'tkazildi"}


@app.post("/api/admin/centers/{center_id}/restore")
async def admin_restore_center(center_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("UPDATE centers SET deleted_at=NULL,is_active=TRUE WHERE id=$1 RETURNING name", center_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
    return {"message": f"{row['name']} tiklandi"}


@app.delete("/api/admin/centers/{center_id}")
async def admin_purge_center(center_id: int, confirm_name: str, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            center = await conn.fetchrow("SELECT name,deleted_at FROM centers WHERE id=$1 FOR UPDATE", center_id)
            if not center:
                raise HTTPException(status_code=404, detail="Tashkilot topilmadi")
            if not center["deleted_at"]:
                raise HTTPException(status_code=409, detail="Avval tashkilotni savatga o'tkazing")
            if confirm_name.strip() != center["name"]:
                raise HTTPException(status_code=400, detail="Tashkilot nomi mos kelmadi")
            await conn.execute("UPDATE users SET role='student',center_id=NULL,group_id=NULL WHERE center_id=$1", center_id)
            await conn.execute("DELETE FROM centers WHERE id=$1", center_id)
    return {"message": "Tashkilot va unga bog'liq ma'lumotlar butunlay o'chirildi"}


@app.post("/api/admin/entities/{entity_type}/{entity_id}/trash")
async def admin_trash_entity(entity_type: str, entity_id: int, _: None = Depends(require_admin)):
    config = ADMIN_ENTITY_CONFIG.get(entity_type)
    if not config:
        raise HTTPException(status_code=400, detail="Obyekt turi noto'g'ri")
    table, label_col, active_col = config
    if entity_type == "user":
        sql = f"UPDATE {table} SET deleted_at=NOW(),is_suspended=TRUE WHERE id=$1 RETURNING {label_col} AS label"
    elif entity_type == "test":
        sql = f"UPDATE {table} SET deleted_at=NOW(),status='archived' WHERE id=$1 RETURNING {label_col} AS label"
    elif active_col:
        sql = f"UPDATE {table} SET deleted_at=NOW(),{active_col}=FALSE WHERE id=$1 RETURNING {label_col} AS label"
    else:
        sql = f"UPDATE {table} SET deleted_at=NOW() WHERE id=$1 RETURNING {label_col} AS label"
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(sql, entity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Obyekt topilmadi")
    return {"message": f"{row['label'] or 'Obyekt'} savatga o'tkazildi"}


@app.post("/api/admin/entities/{entity_type}/{entity_id}/restore")
async def admin_restore_entity(entity_type: str, entity_id: int, _: None = Depends(require_admin)):
    config = ADMIN_ENTITY_CONFIG.get(entity_type)
    if not config:
        raise HTTPException(status_code=400, detail="Obyekt turi noto'g'ri")
    table, label_col, active_col = config
    if entity_type == "user":
        sql = f"UPDATE {table} SET deleted_at=NULL,is_suspended=FALSE WHERE id=$1 RETURNING {label_col} AS label"
    elif entity_type == "test":
        sql = f"UPDATE {table} SET deleted_at=NULL,status='draft' WHERE id=$1 RETURNING {label_col} AS label"
    elif active_col:
        sql = f"UPDATE {table} SET deleted_at=NULL,{active_col}=TRUE WHERE id=$1 RETURNING {label_col} AS label"
    else:
        sql = f"UPDATE {table} SET deleted_at=NULL WHERE id=$1 RETURNING {label_col} AS label"
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(sql, entity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Obyekt topilmadi")
    return {"message": f"{row['label'] or 'Obyekt'} tiklandi"}


@app.delete("/api/admin/entities/{entity_type}/{entity_id}")
async def admin_purge_entity(entity_type: str, entity_id: int, _: None = Depends(require_admin)):
    config = ADMIN_ENTITY_CONFIG.get(entity_type)
    if not config:
        raise HTTPException(status_code=400, detail="Obyekt turi noto'g'ri")
    table, _, _ = config
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT deleted_at FROM {table} WHERE id=$1 FOR UPDATE", entity_id)
            if not row:
                raise HTTPException(status_code=404, detail="Obyekt topilmadi")
            if not row["deleted_at"]:
                raise HTTPException(status_code=409, detail="Avval obyektni savatga o'tkazing")
            if entity_type == "user":
                await conn.execute("UPDATE users SET referred_by=NULL WHERE referred_by=$1", entity_id)
            await conn.execute(f"DELETE FROM {table} WHERE id=$1", entity_id)
    return {"message": "Obyekt butunlay o'chirildi"}


@app.post("/api/admin/users/{user_id}/suspend")
async def admin_suspend_user(user_id: int, _: None = Depends(require_admin)):
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
async def admin_delete_user(user_id: int, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET deleted_at=NOW(),is_suspended=TRUE WHERE id=$1 RETURNING id", user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    return {"message": "Foydalanuvchi savatga o'tkazildi"}


@app.post("/api/admin/grade-writing")
async def grade_writing(data: GradeWriting, _: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE exam_results
            SET writing_band = $1, writing_feedback = $2,
                writing_task_achievement = $4, writing_coherence_cohesion = $5,
                writing_lexical_resource = $6, writing_grammar_accuracy = $7,
                grader_name = $8, writing_graded_at = NOW()
            WHERE id = $3
            RETURNING full_name, email, section
            """,
            data.band, data.feedback, data.result_id,
            data.task_achievement, data.coherence_cohesion, data.lexical_resource, data.grammar_accuracy, "Admin"
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
                elif r["score"] is not None and r["total"] is not None:
                    r_band = get_band_score(r["score"], r["total"], r["section"])
                else:
                    r_band = None
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
async def notify_result(result_id: int, _: None = Depends(require_admin)):
    """Listening/Reading natijasini emailga yuborish"""
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
            elif r["score"] is not None and r["total"] is not None:
                r_band = get_band_score(r["score"], r["total"], r["section"])
            else:
                r_band = None
            sections.append({
                "section": r["section"],
                "score": r["score"],
                "total": r["total"],
                "band": r_band
            })

    band = get_band_score(row["score"], row["total"], row["section"]) if row["score"] is not None and row["total"] is not None else None
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
async def admin_stats(days: int = 30, _: None = Depends(require_admin)):
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
        elif r["score"] is not None and r["total"] is not None:
            b = get_band_score(r["score"], r["total"], s)
        else:
            b = None
            
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
async def export_results(format: str = "csv", _: None = Depends(require_admin)):
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
        b = float(r["writing_band"]) if r["section"] == "writing" and r["writing_band"] is not None else get_band_score(r["score"], r["total"], r["section"]) if r["section"] != "writing" and r["score"] is not None and r["total"] is not None else ""
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
async def export_users(format: str = "csv", _: None = Depends(require_admin)):
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


# ─── Leaderboard (Reyting) ─────────────────────────────────────────────────────

def _compute_overall(row) -> float | None:
    """Talabaning barcha bo'limlar bo'yicha overall band ni hisoblash."""
    from scoring import calculate_overall_band
    bands = []
    for section in ["listening", "reading", "writing", "speaking"]:
        if section in ("writing", "speaking"):
            key = f"{section}_band"
            val = row.get(key)
            if val is not None:
                bands.append(float(val))
        else:
            b = get_band_score(row.get("score"), row.get("total"), section)
            # Bu yerda row section-specific emas, shuning uchun alohida yo'l bilan
    return calculate_overall_band(bands) if bands else None


async def _build_leaderboard(conn, where_clause: str, args: tuple, limit: int = 50) -> list:
    """Leaderboard ma'lumotlarini DB dan olib, band hisoblash."""
    from scoring import get_band_score, calculate_overall_band

    rows = await conn.fetch(
        f"""
        SELECT
            u.id, u.full_name, u.email,
            g.name AS group_name,
            c.name AS center_name,
            BOOL_OR(er.section = 'listening') AS has_listening,
            MAX(CASE WHEN er.section = 'listening' THEN er.score END) AS l_score,
            MAX(CASE WHEN er.section = 'listening' THEN er.total END) AS l_total,
            MAX(CASE WHEN er.section = 'reading' THEN er.score END) AS r_score,
            MAX(CASE WHEN er.section = 'reading' THEN er.total END) AS r_total,
            MAX(CASE WHEN er.section = 'writing' THEN er.writing_band END) AS w_band,
            MAX(CASE WHEN er.section = 'speaking' THEN er.speaking_band END) AS s_band
        FROM users u
        LEFT JOIN exam_results er ON er.email = u.email
        LEFT JOIN groups g ON g.id = u.group_id
        LEFT JOIN centers c ON c.id = u.center_id
        {where_clause}
        GROUP BY u.id, u.full_name, u.email, g.name, c.name
        HAVING COUNT(er.id) > 0
        LIMIT {limit}
        """,
        *args
    )

    result = []
    for row in rows:
        l_band = get_band_score(row["l_score"], row["l_total"], "listening") if row["l_score"] is not None else None
        r_band = get_band_score(row["r_score"], row["r_total"], "reading") if row["r_score"] is not None else None
        w_band = float(row["w_band"]) if row["w_band"] is not None else None
        s_band = float(row["s_band"]) if row["s_band"] is not None else None

        all_bands = [b for b in [l_band, r_band, w_band, s_band] if b is not None]
        overall = calculate_overall_band(all_bands) if all_bands else None

        result.append({
            "id": row["id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "group_name": row["group_name"],
            "center_name": row["center_name"],
            "listening_band": l_band,
            "reading_band": r_band,
            "writing_band": w_band,
            "speaking_band": s_band,
            "overall_band": overall
        })

    # Overall band bo'yicha tartib
    result.sort(key=lambda x: x["overall_band"] or 0, reverse=True)
    for i, item in enumerate(result):
        item["rank"] = i + 1
    return result


@app.get("/api/student/leaderboard")
async def student_leaderboard(current_user: dict = Depends(get_current_user)):
    """O'quvchi o'z guruhidagi reyting jadvalini ko'radi."""
    db = await get_pool()
    async with db.acquire() as conn:
        group_id = current_user.get("group_id")
        if not group_id:
            return {"leaderboard": [], "my_rank": None}

        data = await _build_leaderboard(conn, "WHERE u.group_id = $1 AND u.role = 'student'", (group_id,))

    my_rank = next((item["rank"] for item in data if item["email"] == current_user["email"]), None)
    return {"leaderboard": data[:20], "my_rank": my_rank, "total": len(data)}


@app.get("/api/student/resubmissions")
async def student_resubmissions(current_user: dict = Depends(get_current_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT rr.id,rr.section,rr.reason,rr.due_at,rr.status,rr.created_at,
                   COALESCE(t.title,er.test_slug,'IELTS Mock SS') AS test_title
            FROM resubmission_requests rr
            JOIN exam_results er ON er.id=rr.result_id
            LEFT JOIN tests t ON t.id=er.test_id
            WHERE rr.student_id=$1 ORDER BY rr.created_at DESC
            """,
            current_user["id"]
        )
    return [dict(r) | {
        "due_at": r["due_at"].isoformat() if r["due_at"] else None,
        "created_at": r["created_at"].isoformat(),
    } for r in rows]


@app.get("/api/admin/grade-speaking")
async def admin_grade_speaking_form(_: None = Depends(require_admin)):
    """Admin uchun baholanmagan speaking ro'yxati."""
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT er.id, er.full_name, er.email, er.speaking_telegram_file_id, er.submitted_at,
                   u.id AS student_id, g.name AS group_name
            FROM exam_results er
            LEFT JOIN users u ON u.email = er.email
            LEFT JOIN groups g ON g.id = u.group_id
            WHERE er.section = 'speaking' AND er.speaking_band IS NULL
            ORDER BY er.submitted_at ASC
            """
        )
    return [
        {
            "id": r["id"],
            "student_id": r["student_id"],
            "full_name": r["full_name"],
            "email": r["email"],
            "group_name": r["group_name"],
            "telegram_file_id": r["speaking_telegram_file_id"],
            "submitted_at": r["submitted_at"].isoformat()
        }
        for r in rows
    ]


class GradeSpeakingAdmin(BaseModel):
    result_id: int
    band: float
    feedback: Optional[str] = None
    send_notification: bool = True


@app.post("/api/admin/grade-speaking")
async def admin_grade_speaking(data: GradeSpeakingAdmin, _: None = Depends(require_admin)):
    """Admin speaking ni baholaydi."""
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE exam_results
            SET speaking_band = $1, speaking_feedback = $2,
                grader_name = 'Admin', speaking_graded_at = NOW()
            WHERE id = $3 AND section = 'speaking'
            RETURNING full_name, email
            """,
            data.band, data.feedback, data.result_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Natija topilmadi")

    if data.send_notification:
        try:
            async with db.acquire() as conn2:
                user_row = await conn2.fetchrow(
                    "SELECT telegram_chat_id FROM users WHERE email = $1", row["email"].lower()
                )
            if user_row and user_row["telegram_chat_id"]:
                from telegram import notify_user_speaking_graded
                await notify_user_speaking_graded(
                    user_row["telegram_chat_id"], row["full_name"], data.band, data.feedback
                )
        except Exception:
            pass

    return {"message": "Speaking baholandi"}


# ─── Static Files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.head("/")
async def root_healthcheck():
    return Response(status_code=200)

@app.get("/admin")
async def admin_page():
    return FileResponse("static/admin.html")

@app.get("/profile")
async def profile_page():
    return FileResponse("static/profile.html")

@app.get("/head-teacher")
async def head_teacher_page():
    return FileResponse("static/head-teacher.html")

@app.get("/teacher")
async def teacher_page():
    return FileResponse("static/teacher.html")

@app.get("/school-staff")
async def school_staff_page():
    return FileResponse("static/school-staff.html")

@app.get("/tests")
async def tests_page():
    return FileResponse("static/tests.html")

@app.get("/mock-mode")
async def mock_mode_page():
    return FileResponse("static/mock-mode.html")

@app.get("/mock-result")
async def mock_result_page():
    return FileResponse("static/mock-result.html")

@app.get("/tests/{test_id}/run")
async def test_runner_page(test_id: int):
    return FileResponse("static/test-runner.html")

@app.get("/tests/{test_id}/mode")
async def test_mode_page(test_id: int):
    return FileResponse("static/test-mode.html")

@app.get("/listening-demo")
async def listening_demo_page():
    return FileResponse("static/Listening-demo.html")

@app.get("/reading-demo")
async def reading_demo_page():
    return FileResponse("static/Reading-demo.html")

@app.get("/writing-demo")
async def writing_demo_page():
    return FileResponse("static/writing-demo.html")

@app.get("/speaking-demo")
async def speaking_demo_page():
    return FileResponse("static/speaking-demo.html")

@app.get("/register")
async def register_page():
    # teacher_invite va group_invite linklar shu URL ga keladi
    return FileResponse("static/profile.html")

@app.get("/{path:path}")
async def catch_all(path: str):
    return FileResponse("static/index.html")
