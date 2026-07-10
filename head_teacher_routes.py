from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from auth import FRONTEND_BASE_URL, get_current_head_teacher
from db import get_pool
from groups_db import DEFAULT_MAX_GROUPS_PER_CENTER, DEFAULT_MAX_STUDENTS_PER_CENTER
from result_export import build_results_export
from scoring import get_band_score

router = APIRouter(prefix="/api/head-teacher", tags=["head-teacher"])


class GroupCreateIn(BaseModel):
    name: str


@router.get("/center")
async def get_center(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        center = await conn.fetchrow(
            """
            SELECT id, name, is_active, max_groups, max_students, created_at
            FROM centers WHERE id=$1
            """,
            current_user["center_id"],
        )
        if not center:
            raise HTTPException(status_code=404, detail="Markaz topilmadi")
        groups_count = await conn.fetchval(
            "SELECT COUNT(*) FROM groups WHERE center_id=$1", center["id"]
        )
        students_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE center_id=$1 AND role='student'",
            center["id"],
        )
        return {
            "id": center["id"],
            "name": center["name"],
            "is_active": center["is_active"],
            "created_at": center["created_at"].isoformat(),
            "groups_count": groups_count,
            "students_count": students_count,
            "max_groups": center["max_groups"] or DEFAULT_MAX_GROUPS_PER_CENTER,
            "max_students": center["max_students"] or DEFAULT_MAX_STUDENTS_PER_CENTER,
        }


@router.get("/groups")
async def list_groups(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.id, g.name, g.invite_code, g.is_active, g.created_at,
                   g.teacher_id, g.teacher_invite_code,
                   g.teacher_invite_created_at, g.teacher_invite_expires_at,
                   g.teacher_invite_revoked_at,
                   t.full_name AS teacher_name, t.email AS teacher_email,
                   COUNT(u.id) FILTER (WHERE u.role = 'student') AS students_count
            FROM groups g
            LEFT JOIN users t ON t.id = g.teacher_id
            LEFT JOIN users u ON u.group_id = g.id
            WHERE g.center_id = $1
            GROUP BY g.id, t.full_name, t.email
            ORDER BY g.created_at DESC
            """,
            current_user["center_id"],
        )
        now = datetime.now().astimezone()
        output = []
        for row in rows:
            expires_at = row["teacher_invite_expires_at"]
            active_invite = bool(
                row["teacher_invite_code"]
                and expires_at
                and expires_at > now
                and not row["teacher_id"]
            )
            output.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "invite_code": row["invite_code"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "has_teacher": row["teacher_id"] is not None,
                    "teacher_name": row["teacher_name"],
                    "teacher_email": row["teacher_email"],
                    "students_count": row["students_count"],
                    "has_active_teacher_invite": active_invite,
                    "teacher_invite_created_at": (
                        row["teacher_invite_created_at"].isoformat()
                        if row["teacher_invite_created_at"]
                        else None
                    ),
                    "teacher_invite_expires_at": (
                        expires_at.isoformat() if expires_at else None
                    ),
                    "teacher_invite_revoked_at": (
                        row["teacher_invite_revoked_at"].isoformat()
                        if row["teacher_invite_revoked_at"]
                        else None
                    ),
                }
            )
        return output


@router.post("/groups")
async def create_group(
    data: GroupCreateIn,
    current_user: dict = Depends(get_current_head_teacher),
):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Guruh nomini kiriting")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Guruh nomi 100 belgidan oshmasin")

    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            center = await conn.fetchrow(
                "SELECT is_active, max_groups FROM centers WHERE id=$1 FOR UPDATE",
                current_user["center_id"],
            )
            if not center or not center["is_active"]:
                raise HTTPException(status_code=400, detail="Markaz faol emas")
            max_groups = center["max_groups"] or DEFAULT_MAX_GROUPS_PER_CENTER
            current_count = await conn.fetchval(
                "SELECT COUNT(*) FROM groups WHERE center_id=$1",
                current_user["center_id"],
            )
            if current_count >= max_groups:
                raise HTTPException(
                    status_code=400,
                    detail=f"Guruhlar limiti ({max_groups}) to'lgan",
                )
            invite_code = secrets.token_urlsafe(6)
            row = await conn.fetchrow(
                """
                INSERT INTO groups (name, invite_code, center_id)
                VALUES ($1, $2, $3)
                RETURNING id, name, invite_code, is_active, created_at
                """,
                name,
                invite_code,
                current_user["center_id"],
            )
            return {
                "id": row["id"],
                "name": row["name"],
                "invite_code": row["invite_code"],
                "is_active": row["is_active"],
                "created_at": row["created_at"].isoformat(),
            }


async def _own_group_or_404(conn, group_id: int, center_id: int):
    group = await conn.fetchrow("SELECT * FROM groups WHERE id=$1", group_id)
    if not group or group["center_id"] != center_id:
        raise HTTPException(status_code=404, detail="Guruh topilmadi")
    return group


@router.post("/groups/{group_id}/generate-teacher-invite")
async def generate_teacher_invite(
    group_id: int,
    expires_in_hours: int = Query(default=48, ge=1, le=168),
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_group_or_404(conn, group_id, current_user["center_id"])
        if group["teacher_id"]:
            raise HTTPException(
                status_code=400,
                detail="Bu guruhda allaqachon teacher bor — avval uni olib tashlang",
            )
        if not group["is_active"]:
            raise HTTPException(status_code=400, detail="Yopilgan guruh uchun taklif yaratib bo'lmaydi")

        invite_code = secrets.token_urlsafe(24)
        row = await conn.fetchrow(
            """
            UPDATE groups
            SET teacher_invite_code=$1,
                teacher_invite_created_at=NOW(),
                teacher_invite_expires_at=NOW() + ($2::int * INTERVAL '1 hour'),
                teacher_invite_revoked_at=NULL
            WHERE id=$3
            RETURNING teacher_invite_created_at, teacher_invite_expires_at
            """,
            invite_code,
            expires_in_hours,
            group_id,
        )
        invite_link = (
            f"{FRONTEND_BASE_URL.rstrip('/')}/teacher"
            f"?teacher_invite={invite_code}"
        )
        return {
            "teacher_invite_code": invite_code,
            "invite_link": invite_link,
            "created_at": row["teacher_invite_created_at"].isoformat(),
            "expires_at": row["teacher_invite_expires_at"].isoformat(),
            "expires_in_hours": expires_in_hours,
        }


@router.post("/groups/{group_id}/revoke-teacher-invite")
async def revoke_teacher_invite(
    group_id: int,
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        group = await _own_group_or_404(conn, group_id, current_user["center_id"])
        if group["teacher_id"]:
            raise HTTPException(status_code=400, detail="Teacher allaqachon guruhga biriktirilgan")
        if not group["teacher_invite_code"]:
            raise HTTPException(status_code=400, detail="Bekor qilinadigan faol taklif yo'q")
        await conn.execute(
            """
            UPDATE groups
            SET teacher_invite_code=NULL,
                teacher_invite_created_at=NULL,
                teacher_invite_expires_at=NULL,
                teacher_invite_revoked_at=NOW()
            WHERE id=$1
            """,
            group_id,
        )
        return {"message": "Teacher taklif havolasi bekor qilindi"}


@router.post("/groups/{group_id}/remove-teacher")
async def remove_teacher(
    group_id: int,
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            group = await _own_group_or_404(conn, group_id, current_user["center_id"])
            if not group["teacher_id"]:
                raise HTTPException(status_code=400, detail="Bu guruhda teacher yo'q")
            await conn.execute(
                "UPDATE users SET role='student', center_id=NULL WHERE id=$1",
                group["teacher_id"],
            )
            await conn.execute(
                """
                UPDATE groups
                SET teacher_id=NULL,
                    teacher_invite_code=NULL,
                    teacher_invite_created_at=NULL,
                    teacher_invite_expires_at=NULL
                WHERE id=$1
                """,
                group_id,
            )
            return {
                "message": "Teacher guruhdan olib tashlandi, hisobi student sifatida saqlandi"
            }


@router.post("/groups/{group_id}/deactivate")
async def toggle_group(
    group_id: int,
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        await _own_group_or_404(conn, group_id, current_user["center_id"])
        row = await conn.fetchrow(
            "UPDATE groups SET is_active = NOT is_active WHERE id=$1 RETURNING is_active",
            group_id,
        )
        return {
            "message": "Guruh yopildi" if not row["is_active"] else "Guruh qayta ochildi",
            "is_active": row["is_active"],
        }


@router.post("/groups/{group_id}/import-students")
async def import_students(
    group_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_head_teacher),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Faqat .csv fayl qabul qilinadi")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV fayl 5 MB dan oshmasin")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1251", errors="ignore")

    reader = csv.DictReader(io.StringIO(content))
    fieldnames = [field.strip().lower() for field in (reader.fieldnames or [])]
    if "email" not in fieldnames:
        raise HTTPException(status_code=400, detail="CSV faylida 'email' ustuni topilmadi")

    db = await get_pool()
    async with db.acquire() as conn:
        await _own_group_or_404(conn, group_id, current_user["center_id"])
        added, skipped, invalid = 0, 0, 0
        async with conn.transaction():
            for raw_row in reader:
                norm = {
                    (key or "").strip().lower(): (value or "").strip()
                    for key, value in raw_row.items()
                }
                email = norm.get("email", "").lower()
                full_name = norm.get("full_name") or norm.get("full name") or None
                if not email or "@" not in email:
                    invalid += 1
                    continue
                result = await conn.execute(
                    """
                    INSERT INTO group_roster_emails (group_id, email, full_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (group_id, email) DO NOTHING
                    """,
                    group_id,
                    email,
                    full_name,
                )
                if result == "INSERT 0 1":
                    added += 1
                else:
                    skipped += 1
        return {
            "added": added,
            "skipped_duplicates": skipped,
            "invalid_rows": invalid,
        }


@router.get("/groups/{group_id}/roster")
async def get_roster(
    group_id: int,
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        await _own_group_or_404(conn, group_id, current_user["center_id"])
        rows = await conn.fetch(
            """
            SELECT email, full_name, used, created_at
            FROM group_roster_emails
            WHERE group_id=$1
            ORDER BY created_at DESC
            """,
            group_id,
        )
        return [
            {
                "email": row["email"],
                "full_name": row["full_name"],
                "used": row["used"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]


@router.get("/export")
async def export_center_results(
    format: str = Query(default="xlsx", pattern="^(csv|xlsx)$"),
    group_id: Optional[int] = Query(default=None, ge=1),
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        group_name = "all-groups"
        if group_id is not None:
            group = await _own_group_or_404(conn, group_id, current_user["center_id"])
            group_name = group["name"]
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
            WHERE u.center_id=$1 AND u.role='student'
              AND ($2::int IS NULL OR u.group_id=$2)
            ORDER BY g.name, u.full_name, er.submitted_at DESC
            """,
            current_user["center_id"],
            group_id,
        )
    return build_results_export(
        rows,
        format,
        f"head-teacher-{group_name}-results",
    )


@router.get("/stats")
async def head_teacher_stats(
    days: int = Query(default=30, ge=7, le=90),
    current_user: dict = Depends(get_current_head_teacher),
):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT er.section, er.score, er.total, er.writing_band, er.submitted_at
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.center_id = $1 AND u.role = 'student'
              AND er.submitted_at >= NOW() - ($2::int * INTERVAL '1 day')
            ORDER BY er.submitted_at
            """,
            current_user["center_id"],
            days,
        )
        all_rows = await conn.fetch(
            """
            SELECT er.section, er.score, er.total, er.writing_band
            FROM exam_results er
            JOIN users u ON u.email = er.email
            WHERE u.center_id = $1 AND u.role = 'student'
            """,
            current_user["center_id"],
        )

    bands_by_section: dict[str, list[float]] = {}
    for row in all_rows:
        if row["section"] == "writing":
            band = row["writing_band"]
        else:
            band = (
                get_band_score(row["score"], row["total"], row["section"])
                if row["score"] is not None and row["total"]
                else None
            )
        if band is not None:
            bands_by_section.setdefault(row["section"], []).append(float(band))

    averages = {
        section: round(sum(values) / len(values), 1)
        for section, values in bands_by_section.items()
    }
    weakest_section = min(averages, key=averages.get) if averages else None

    today = datetime.utcnow().date()
    trend_map = {
        today - timedelta(days=offset): {"attempts": 0, "bands": []}
        for offset in range(days - 1, -1, -1)
    }
    for row in rows:
        day = row["submitted_at"].date()
        if day not in trend_map:
            continue
        trend_map[day]["attempts"] += 1
        if row["section"] == "writing":
            band = row["writing_band"]
        else:
            band = (
                get_band_score(row["score"], row["total"], row["section"])
                if row["score"] is not None and row["total"]
                else None
            )
        if band is not None:
            trend_map[day]["bands"].append(float(band))

    trend = []
    for day, values in trend_map.items():
        daily_bands = values["bands"]
        trend.append(
            {
                "date": day.isoformat(),
                "attempts": values["attempts"],
                "average_band": (
                    round(sum(daily_bands) / len(daily_bands), 2)
                    if daily_bands
                    else None
                ),
            }
        )

    return {
        "average_band_by_section": averages,
        "weakest_section": weakest_section,
        "total_attempts": len(all_rows),
        "trend_days": days,
        "trend": trend,
    }
