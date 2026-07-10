from fastapi import APIRouter, Depends, HTTPException

from db import get_pool
from auth import get_current_teacher
from scoring import get_band_score, calculate_overall_band

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

    return {
        "id": group["id"], "name": group["name"], "invite_code": group["invite_code"],
        "created_at": group["created_at"].isoformat(), "students_count": students_count,
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
            SELECT section, score, total, writing_band, submitted_at
            FROM exam_results WHERE email=$1
            ORDER BY submitted_at DESC
            """,
            student["email"]
        )

    return {
        "student": dict(student),
        "history": [
            {
                "section": h["section"], "score": h["score"], "total": h["total"],
                "writing_band": float(h["writing_band"]) if h["writing_band"] is not None else None,
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


