from fastapi import APIRouter, Depends, HTTPException

from db import get_pool
from auth import get_current_teacher
from scoring import get_band_score, calculate_overall_band
from branding import branding_payload

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


async def _own_active_group_or_error(conn, teacher_id: int):
    group = await conn.fetchrow(
        "SELECT id, name, invite_code, is_active, created_at FROM groups WHERE teacher_id=$1",
        teacher_id
    )
    if not group:
        raise HTTPException(status_code=404, detail="Sizga biriktirilgan guruh topilmadi")
    if not group["is_active"]:
        raise HTTPException(status_code=403, detail="Guruhingiz markaz rahbari tomonidan yopilgan")
    return group


def _band_for(row) -> float | None:
    if row["section"] == "writing":
        return float(row["writing_band"]) if row["writing_band"] is not None else None
    if row["score"] is not None and row["total"] is not None:
        band = get_band_score(row["score"], row["total"], row["section"])
        return float(band) if band is not None else None
    return None


@router.get("/group")
async def get_group(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        students_count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE group_id=$1", group["id"])
        center = await conn.fetchrow(
            """
            SELECT id, name, organization_type, slug, brand_name, brand_primary_color,
                   brand_secondary_color, brand_logo_url, brand_favicon_url,
                   brand_contact_email, brand_contact_phone, show_powered_by
            FROM centers WHERE id=$1
            """,
            current_user["center_id"]
        )

    return {
        "id": group["id"], "name": group["name"], "invite_code": group["invite_code"],
        "created_at": group["created_at"].isoformat(), "students_count": students_count,
        "branding": branding_payload(center) if center else None,
    }


@router.get("/students")
async def list_students(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        students = await conn.fetch(
            """
            SELECT u.id, u.username, u.full_name, u.email, u.email_verified,
                   COUNT(er.id) AS total_attempts
            FROM users u
            LEFT JOIN exam_results er ON er.email = u.email
            WHERE u.group_id = $1
            GROUP BY u.id
            ORDER BY u.full_name
            """,
            group["id"]
        )

        result = []
        for s in students:
            latest = await conn.fetch(
                """
                SELECT DISTINCT ON (section) section, score, total, writing_band
                FROM exam_results WHERE email = $1
                ORDER BY section, submitted_at DESC
                """,
                s["email"]
            )
            bands = [b for b in (_band_for(r) for r in latest) if b is not None]
            result.append({
                "id": s["id"], "username": s["username"], "full_name": s["full_name"],
                "email": s["email"], "email_verified": s["email_verified"],
                "total_attempts": s["total_attempts"],
                "overall_band": calculate_overall_band(bands) if bands else None,
            })

    return result


@router.get("/students/{student_id}")
async def get_student(student_id: int, current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        # MUHIM: student ID orqali qidirilganda ham group_id tekshiruvi shart — IDOR himoyasi
        student = await conn.fetchrow(
            "SELECT id, username, full_name, email, email_verified FROM users WHERE id=$1 AND group_id=$2",
            student_id, group["id"]
        )
        if not student:
            raise HTTPException(status_code=404, detail="Talaba topilmadi")

        history = await conn.fetch(
            """
            SELECT er.section, er.score, er.total, er.writing_band, er.submitted_at,
                   COALESCE(t.title, er.test_slug, 'IELTS Mock SS') AS test_title
            FROM exam_results er LEFT JOIN tests t ON t.id=er.test_id WHERE er.email=$1
            ORDER BY er.submitted_at DESC
            """,
            student["email"]
        )

    return {
        "student": dict(student),
        "history": [
            {
                "section": h["section"], "score": h["score"], "total": h["total"],
                "writing_band": float(h["writing_band"]) if h["writing_band"] is not None else None,
                "test_title": h["test_title"],
                "submitted_at": h["submitted_at"].isoformat(),
            }
            for h in history
        ],
    }


@router.get("/stats")
async def teacher_stats(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT er.section, er.score, er.total, er.writing_band
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.group_id = $1
            """,
            group["id"]
        )

    bands_by_section = {}
    for r in rows:
        band = _band_for(r)
        if band is not None:
            bands_by_section.setdefault(r["section"], []).append(band)

    averages = {s: round(sum(v) / len(v), 1) for s, v in bands_by_section.items()}
    weakest_section = min(averages, key=averages.get) if averages else None

    return {
        "average_band_by_section": averages,
        "weakest_section": weakest_section,
        "total_attempts": len(rows),
    }


from pydantic import BaseModel
from typing import Optional

class GradeWritingTeacher(BaseModel):
    band: float
    feedback: Optional[str] = None
    task_achievement: Optional[float] = None
    coherence_cohesion: Optional[float] = None
    lexical_resource: Optional[float] = None
    grammar_accuracy: Optional[float] = None

@router.get("/pending-writing")
async def get_pending_writing(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT er.id, er.full_name, er.email, er.writing_task1, er.writing_task2, er.submitted_at, u.id AS student_id
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.group_id = $1 AND er.section = 'writing' AND er.writing_band IS NULL
            ORDER BY er.submitted_at ASC
            """,
            group["id"]
        )
    return [
        {
            "id": r["id"],
            "student_id": r["student_id"],
            "full_name": r["full_name"],
            "email": r["email"],
            "writing_task1": r["writing_task1"],
            "writing_task2": r["writing_task2"],
            "submitted_at": r["submitted_at"].isoformat()
        }
        for r in rows
    ]


@router.post("/students/{student_id}/grade-writing/{result_id}")
async def grade_student_writing(student_id: int, result_id: int, data: GradeWritingTeacher, current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        
        student = await conn.fetchrow("SELECT email, full_name FROM users WHERE id=$1 AND group_id=$2", student_id, group["id"])
        if not student:
            raise HTTPException(status_code=404, detail="Talaba topilmadi")

        row = await conn.fetchrow(
            """
            UPDATE exam_results
            SET writing_band = $1, writing_feedback = $2,
                writing_task_achievement = $4, writing_coherence_cohesion = $5,
                writing_lexical_resource = $6, writing_grammar_accuracy = $7,
                grader_name = $8, writing_graded_at = NOW()
            WHERE id = $3 AND email = $9 AND section = 'writing'
            RETURNING id
            """,
            data.band, data.feedback, result_id,
            data.task_achievement, data.coherence_cohesion, data.lexical_resource, data.grammar_accuracy,
            current_user["full_name"], student["email"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Natija topilmadi")

    try:
        from main import build_result_email, send_email
        html = build_result_email(student["full_name"], "writing", None, None, data.band, data.feedback)
        await send_email(student["email"], student["full_name"], "IELTS Mock — Writing natijangiz baholandi", html)
    except Exception:
        pass

    return {"message": "Baholandi"}


class GradeSpeakingTeacher(BaseModel):
    band: float
    feedback: Optional[str] = None


@router.get("/pending-speaking")
async def get_pending_speaking(current_user: dict = Depends(get_current_teacher)):
    """O'qituvchi baholanmagan speaking audiolari ro'yxatini oladi."""
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT er.id, er.full_name, er.email, er.speaking_telegram_file_id, er.submitted_at, u.id AS student_id
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.group_id = $1 AND er.section = 'speaking' AND er.speaking_band IS NULL
            ORDER BY er.submitted_at ASC
            """,
            group["id"]
        )
    return [
        {
            "id": r["id"],
            "student_id": r["student_id"],
            "full_name": r["full_name"],
            "email": r["email"],
            "telegram_file_id": r["speaking_telegram_file_id"],
            "submitted_at": r["submitted_at"].isoformat()
        }
        for r in rows
    ]


@router.post("/students/{student_id}/grade-speaking/{result_id}")
async def grade_student_speaking(
    student_id: int,
    result_id: int,
    data: GradeSpeakingTeacher,
    current_user: dict = Depends(get_current_teacher)
):
    """O'qituvchi speaking ni baholaydi."""
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        student = await conn.fetchrow(
            "SELECT email, full_name FROM users WHERE id=$1 AND group_id=$2",
            student_id, group["id"]
        )
        if not student:
            raise HTTPException(status_code=404, detail="Talaba topilmadi")

        row = await conn.fetchrow(
            """
            UPDATE exam_results
            SET speaking_band = $1, speaking_feedback = $2,
                grader_name = $3, speaking_graded_at = NOW()
            WHERE id = $4 AND email = $5 AND section = 'speaking'
            RETURNING id
            """,
            data.band, data.feedback, current_user["full_name"],
            result_id, student["email"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Natija topilmadi")

        user_row = await conn.fetchrow(
            "SELECT telegram_chat_id FROM users WHERE email = $1", student["email"]
        )

    if user_row and user_row["telegram_chat_id"]:
        try:
            from telegram import notify_user_speaking_graded
            await notify_user_speaking_graded(
                user_row["telegram_chat_id"], student["full_name"], data.band, data.feedback
            )
        except Exception:
            pass

    return {"message": "Speaking baholandi"}


@router.get("/leaderboard")
async def teacher_leaderboard(current_user: dict = Depends(get_current_teacher)):
    """O'qituvchi o'z guruhi reyting jadvalini ko'radi."""
    from scoring import get_band_score, calculate_overall_band
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT
                u.id, u.full_name, u.email,
                MAX(CASE WHEN er.section = 'listening' THEN er.score END) AS l_score,
                MAX(CASE WHEN er.section = 'listening' THEN er.total END) AS l_total,
                MAX(CASE WHEN er.section = 'reading'   THEN er.score END) AS r_score,
                MAX(CASE WHEN er.section = 'reading'   THEN er.total END) AS r_total,
                MAX(CASE WHEN er.section = 'writing'   THEN er.writing_band END) AS w_band,
                MAX(CASE WHEN er.section = 'speaking'  THEN er.speaking_band END) AS s_band
            FROM users u
            LEFT JOIN exam_results er ON er.email = u.email
            WHERE u.group_id = $1 AND u.role = 'student'
            GROUP BY u.id, u.full_name, u.email
            """,
            group["id"]
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
            "id": row["id"], "full_name": row["full_name"], "email": row["email"],
            "listening_band": l_band, "reading_band": r_band,
            "writing_band": w_band, "speaking_band": s_band,
            "overall_band": overall
        })

    result.sort(key=lambda x: x["overall_band"] or 0, reverse=True)
    for i, item in enumerate(result):
        item["rank"] = i + 1
    return {"group_name": group["name"], "leaderboard": result}


@router.get("/export/results")
async def export_teacher_results(format: str = "excel", current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT er.id, u.full_name, u.email, er.section, er.score, er.total, er.writing_band,
                   er.writing_task_achievement, er.writing_coherence_cohesion, er.writing_lexical_resource, er.writing_grammar_accuracy,
                   er.grader_name, er.writing_graded_at, er.writing_feedback, er.submitted_at,
                   $1::text AS group_name
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.group_id = $2
            ORDER BY er.submitted_at DESC
            """,
            group["name"], group["id"]
        )
    from result_export import build_results_export
    return build_results_export(rows, format, f"guruh-natijalari")


