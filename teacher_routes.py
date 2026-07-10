from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth import get_current_teacher
from db import get_pool
from result_export import build_results_export
from scoring import calculate_overall_band, get_band_score

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


class WritingGradeIn(BaseModel):
    result_id: int
    task_achievement: float = Field(..., ge=0, le=9)
    coherence_cohesion: float = Field(..., ge=0, le=9)
    lexical_resource: float = Field(..., ge=0, le=9)
    grammar_accuracy: float = Field(..., ge=0, le=9)
    feedback: Optional[str] = Field(default=None, max_length=5000)


async def _own_active_group_or_error(conn, teacher_id: int):
    group = await conn.fetchrow(
        "SELECT id, name, invite_code, is_active, created_at FROM groups WHERE teacher_id=$1",
        teacher_id,
    )
    if not group:
        raise HTTPException(status_code=404, detail="Sizga biriktirilgan guruh topilmadi")
    if not group["is_active"]:
        raise HTTPException(
            status_code=403,
            detail="Guruhingiz markaz rahbari tomonidan yopilgan",
        )
    return group


def _band_for(row) -> float | None:
    if row["section"] == "writing":
        return float(row["writing_band"]) if row["writing_band"] is not None else None
    if row["score"] is not None and row["total"] is not None:
        band = get_band_score(row["score"], row["total"], row["section"])
        return float(band) if band is not None else None
    return None


def _validate_half_band(value: float, label: str) -> Decimal:
    decimal_value = Decimal(str(value))
    if decimal_value < 0 or decimal_value > 9:
        raise HTTPException(status_code=400, detail=f"{label} 0 dan 9 gacha bo'lishi kerak")
    if decimal_value * 2 != (decimal_value * 2).to_integral_value():
        raise HTTPException(
            status_code=400,
            detail=f"{label} faqat 0.5 qadam bilan kiritiladi",
        )
    return decimal_value


def _writing_band(data: WritingGradeIn) -> tuple[float, list[Decimal]]:
    criteria = [
        _validate_half_band(data.task_achievement, "Task Achievement / Response"),
        _validate_half_band(data.coherence_cohesion, "Coherence & Cohesion"),
        _validate_half_band(data.lexical_resource, "Lexical Resource"),
        _validate_half_band(data.grammar_accuracy, "Grammar Range & Accuracy"),
    ]
    average = sum(criteria, Decimal("0")) / Decimal("4")
    rounded = (average * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2
    return float(rounded), criteria


@router.get("/group")
async def get_group(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        students_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE group_id=$1 AND role='student'",
            group["id"],
        )
        return {
            "id": group["id"],
            "name": group["name"],
            "invite_code": group["invite_code"],
            "created_at": group["created_at"].isoformat(),
            "students_count": students_count,
        }


@router.get("/students")
async def list_students(current_user: dict = Depends(get_current_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        students = await conn.fetch(
            """
            SELECT u.id, u.username, u.full_name, u.email, u.email_verified,
                   COUNT(er.id) AS total_attempts,
                   COUNT(er.id) FILTER (
                       WHERE er.section='writing' AND er.writing_band IS NULL
                   ) AS pending_writings
            FROM users u
            LEFT JOIN exam_results er ON er.email = u.email
            WHERE u.group_id = $1 AND u.role='student'
            GROUP BY u.id
            ORDER BY u.full_name
            """,
            group["id"],
        )
        result = []
        for student in students:
            latest = await conn.fetch(
                """
                SELECT DISTINCT ON (section) section, score, total, writing_band
                FROM exam_results
                WHERE email = $1
                ORDER BY section, submitted_at DESC
                """,
                student["email"],
            )
            bands = [band for band in (_band_for(row) for row in latest) if band is not None]
            result.append(
                {
                    "id": student["id"],
                    "username": student["username"],
                    "full_name": student["full_name"],
                    "email": student["email"],
                    "email_verified": student["email_verified"],
                    "total_attempts": student["total_attempts"],
                    "pending_writings": student["pending_writings"],
                    "overall_band": calculate_overall_band(bands) if bands else None,
                }
            )
        return result


@router.get("/students/{student_id}")
async def get_student(
    student_id: int,
    current_user: dict = Depends(get_current_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        # Student ID bilan ishlaganda ham guruh scope tekshiruvi saqlanadi (IDOR himoyasi).
        student = await conn.fetchrow(
            """
            SELECT id, username, full_name, email, email_verified
            FROM users
            WHERE id=$1 AND group_id=$2 AND role='student'
            """,
            student_id,
            group["id"],
        )
        if not student:
            raise HTTPException(status_code=404, detail="Talaba topilmadi")

        history = await conn.fetch(
            """
            SELECT er.id, er.section, er.score, er.total,
                   er.writing_task1, er.writing_task2,
                   er.writing_band, er.writing_feedback,
                   er.writing_task_achievement,
                   er.writing_coherence_cohesion,
                   er.writing_lexical_resource,
                   er.writing_grammar_accuracy,
                   er.writing_graded_at,
                   grader.full_name AS grader_name,
                   er.submitted_at
            FROM exam_results er
            LEFT JOIN users grader ON grader.id = er.writing_graded_by
            WHERE er.email=$1
            ORDER BY er.submitted_at DESC
            """,
            student["email"],
        )
        return {
            "student": dict(student),
            "history": [
                {
                    "id": row["id"],
                    "section": row["section"],
                    "score": row["score"],
                    "total": row["total"],
                    "writing_task1": row["writing_task1"],
                    "writing_task2": row["writing_task2"],
                    "writing_band": (
                        float(row["writing_band"])
                        if row["writing_band"] is not None
                        else None
                    ),
                    "writing_feedback": row["writing_feedback"],
                    "writing_task_achievement": (
                        float(row["writing_task_achievement"])
                        if row["writing_task_achievement"] is not None
                        else None
                    ),
                    "writing_coherence_cohesion": (
                        float(row["writing_coherence_cohesion"])
                        if row["writing_coherence_cohesion"] is not None
                        else None
                    ),
                    "writing_lexical_resource": (
                        float(row["writing_lexical_resource"])
                        if row["writing_lexical_resource"] is not None
                        else None
                    ),
                    "writing_grammar_accuracy": (
                        float(row["writing_grammar_accuracy"])
                        if row["writing_grammar_accuracy"] is not None
                        else None
                    ),
                    "writing_graded_at": (
                        row["writing_graded_at"].isoformat()
                        if row["writing_graded_at"]
                        else None
                    ),
                    "grader_name": row["grader_name"],
                    "submitted_at": row["submitted_at"].isoformat(),
                }
                for row in history
            ],
        }


@router.post("/students/{student_id}/grade-writing")
async def grade_student_writing(
    student_id: int,
    data: WritingGradeIn,
    current_user: dict = Depends(get_current_teacher),
):
    band, criteria = _writing_band(data)
    feedback = (data.feedback or "").strip() or None

    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            group = await _own_active_group_or_error(conn, current_user["id"])
            student = await conn.fetchrow(
                """
                SELECT id, full_name, email
                FROM users
                WHERE id=$1 AND group_id=$2 AND role='student'
                FOR UPDATE
                """,
                student_id,
                group["id"],
            )
            if not student:
                raise HTTPException(status_code=404, detail="Talaba topilmadi")

            row = await conn.fetchrow(
                """
                UPDATE exam_results
                SET writing_band=$1,
                    writing_feedback=$2,
                    writing_task_achievement=$3,
                    writing_coherence_cohesion=$4,
                    writing_lexical_resource=$5,
                    writing_grammar_accuracy=$6,
                    writing_graded_by=$7,
                    writing_graded_at=NOW()
                WHERE id=$8 AND email=$9 AND section='writing'
                RETURNING id, submitted_at, writing_graded_at
                """,
                band,
                feedback,
                criteria[0],
                criteria[1],
                criteria[2],
                criteria[3],
                current_user["id"],
                data.result_id,
                student["email"],
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Writing natijasi topilmadi yoki bu studentga tegishli emas",
                )

    return {
        "message": "Writing bahosi saqlandi",
        "result_id": row["id"],
        "student_id": student_id,
        "student_name": student["full_name"],
        "band": band,
        "criteria": {
            "task_achievement": float(criteria[0]),
            "coherence_cohesion": float(criteria[1]),
            "lexical_resource": float(criteria[2]),
            "grammar_accuracy": float(criteria[3]),
        },
        "feedback": feedback,
        "graded_at": row["writing_graded_at"].isoformat(),
    }


@router.get("/export")
async def export_group_results(
    format: str = Query(default="xlsx", pattern="^(csv|xlsx)$"),
    current_user: dict = Depends(get_current_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_active_group_or_error(conn, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT er.id, g.name AS group_name, u.full_name, u.email,
                   er.section, er.score, er.total, er.writing_band,
                   er.writing_task_achievement,
                   er.writing_coherence_cohesion,
                   er.writing_lexical_resource,
                   er.writing_grammar_accuracy,
                   er.writing_feedback,
                   er.writing_graded_at,
                   grader.full_name AS grader_name,
                   er.submitted_at
            FROM users u
            JOIN groups g ON g.id = u.group_id
            JOIN exam_results er ON er.email = u.email
            LEFT JOIN users grader ON grader.id = er.writing_graded_by
            WHERE u.group_id=$1 AND u.role='student'
            ORDER BY u.full_name, er.submitted_at DESC
            """,
            group["id"],
        )
    return build_results_export(rows, format, f"teacher-{group['name']}-results")


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
            WHERE u.group_id = $1 AND u.role='student'
            """,
            group["id"],
        )
        pending_writings = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM exam_results er
            JOIN users u ON u.email=er.email
            WHERE u.group_id=$1 AND u.role='student'
              AND er.section='writing' AND er.writing_band IS NULL
            """,
            group["id"],
        )

        bands_by_section: dict[str, list[float]] = {}
        for row in rows:
            band = _band_for(row)
            if band is not None:
                bands_by_section.setdefault(row["section"], []).append(band)
        averages = {
            section: round(sum(values) / len(values), 1)
            for section, values in bands_by_section.items()
        }
        weakest_section = min(averages, key=averages.get) if averages else None
        return {
            "average_band_by_section": averages,
            "weakest_section": weakest_section,
            "total_attempts": len(rows),
            "pending_writings": pending_writings,
        }
