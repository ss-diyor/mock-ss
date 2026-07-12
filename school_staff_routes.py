import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from branding import branding_payload
from db import get_pool


router = APIRouter(prefix="/api/school-staff", tags=["school-staff"])


class StudentLookupIn(BaseModel):
    login: str


async def get_current_school_staff(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "school_staff" or not current_user.get("center_id"):
        raise HTTPException(status_code=403, detail="Maktab xodimi huquqi talab qilinadi")
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id AS staff_id, s.center_id, s.employee_code, s.is_active,
                   p.id AS position_id, p.name AS position_name, p.permissions,
                   c.name AS organization_name, c.organization_type, c.is_active AS organization_active
            FROM school_staff s
            JOIN centers c ON c.id=s.center_id
            LEFT JOIN school_positions p ON p.id=s.position_id
            WHERE s.user_id=$1 AND s.center_id=$2
            """,
            current_user["id"], current_user["center_id"]
        )
    if not row or not row["is_active"] or not row["organization_active"] or row["organization_type"] != "school":
        raise HTTPException(status_code=403, detail="Xodim hisobi faol emas")
    raw_permissions = row["permissions"] or []
    permissions = json.loads(raw_permissions) if isinstance(raw_permissions, str) else list(raw_permissions)
    return current_user | dict(row) | {"permissions": permissions}


def require_any_permission(*required: str) -> Callable:
    async def dependency(staff: dict = Depends(get_current_school_staff)) -> dict:
        if not set(required).intersection(staff["permissions"]):
            raise HTTPException(status_code=403, detail="Bu amal uchun ruxsat berilmagan")
        return staff
    return dependency


async def _own_class_or_404(conn, class_id: int, center_id: int):
    row = await conn.fetchrow(
        "SELECT id, name, academic_year, grade_level FROM school_classes WHERE id=$1 AND center_id=$2 AND is_active=TRUE",
        class_id, center_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sinf topilmadi")
    return row


async def _accessible_class_or_404(conn, class_id: int, staff: dict):
    can_manage_all = bool({"manage_classes", "manage_students", "view_reports"}.intersection(staff["permissions"]))
    row = await conn.fetchrow(
        """
        SELECT c.id, c.name, c.academic_year, c.grade_level
        FROM school_classes c
        WHERE c.id=$1 AND c.center_id=$2 AND c.is_active=TRUE
          AND ($3::boolean OR EXISTS (
              SELECT 1 FROM school_teacher_assignments a WHERE a.class_id=c.id AND a.staff_id=$4
          ))
        """,
        class_id, staff["center_id"], can_manage_all, staff["staff_id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sinf topilmadi yoki sizga biriktirilmagan")
    return row


@router.get("/me")
async def staff_me(staff: dict = Depends(get_current_school_staff)):
    db = await get_pool()
    async with db.acquire() as conn:
        center = await conn.fetchrow(
            """
            SELECT id, name, organization_type, slug, brand_name, brand_primary_color,
                   brand_secondary_color, brand_logo_url, brand_favicon_url,
                   brand_contact_email, brand_contact_phone, show_powered_by
            FROM centers WHERE id=$1
            """,
            staff["center_id"]
        )
    return {
        "user": {
            "id": staff["id"], "username": staff["username"], "full_name": staff["full_name"],
            "email": staff["email"], "employee_code": staff["employee_code"],
        },
        "staff_id": staff["staff_id"],
        "position": staff["position_name"],
        "permissions": staff["permissions"],
        "branding": branding_payload(center),
    }


@router.get("/classes")
async def staff_classes(
    staff: dict = Depends(require_any_permission("manage_classes", "manage_students", "view_results"))
):
    db = await get_pool()
    can_manage_all = bool({"manage_classes", "manage_students", "view_reports"}.intersection(staff["permissions"]))
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.name, c.academic_year, c.grade_level,
                   u.full_name AS homeroom_teacher,
                   STRING_AGG(DISTINCT sub.name, ', ' ORDER BY sub.name) AS subjects,
                   COUNT(DISTINCT cs.id) FILTER (WHERE cs.left_at IS NULL) AS students_count
            FROM school_classes c
            LEFT JOIN school_staff hs ON hs.id=c.homeroom_staff_id
            LEFT JOIN users u ON u.id=hs.user_id
            LEFT JOIN school_class_students cs ON cs.class_id=c.id
            LEFT JOIN school_teacher_assignments a ON a.class_id=c.id
            LEFT JOIN school_subjects sub ON sub.id=a.subject_id
            WHERE c.center_id=$1 AND c.is_active=TRUE
              AND ($2::boolean OR EXISTS (
                  SELECT 1 FROM school_teacher_assignments own_a WHERE own_a.class_id=c.id AND own_a.staff_id=$3
              ))
            GROUP BY c.id, u.full_name
            ORDER BY c.academic_year DESC, c.grade_level, c.name
            """,
            staff["center_id"], can_manage_all, staff["staff_id"]
        )
    return [dict(row) for row in rows]


@router.get("/classes/{class_id}/students")
async def class_students(
    class_id: int,
    staff: dict = Depends(require_any_permission("manage_students", "view_results"))
):
    db = await get_pool()
    async with db.acquire() as conn:
        await _accessible_class_or_404(conn, class_id, staff)
        rows = await conn.fetch(
            """
            SELECT u.id, u.full_name, u.username, u.email, u.email_verified,
                   COUNT(er.id) AS attempts
            FROM school_class_students cs
            JOIN users u ON u.id=cs.student_id
            LEFT JOIN exam_results er ON er.email=u.email
            WHERE cs.class_id=$1 AND cs.left_at IS NULL
            GROUP BY u.id ORDER BY u.full_name
            """,
            class_id
        )
    return [dict(row) for row in rows]


@router.post("/classes/{class_id}/students")
async def add_student(
    class_id: int,
    data: StudentLookupIn,
    staff: dict = Depends(require_any_permission("manage_students"))
):
    login = data.login.strip().lower().lstrip("@")
    if not login:
        raise HTTPException(status_code=400, detail="Username yoki email kiriting")
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            await _accessible_class_or_404(conn, class_id, staff)
            user = await conn.fetchrow("SELECT id, role, center_id FROM users WHERE username=$1 OR email=$1", login)
            if not user:
                raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
            if user["role"] != "student":
                raise HTTPException(status_code=400, detail="Faqat student hisobini qo'shish mumkin")
            if user["center_id"] and user["center_id"] != staff["center_id"]:
                raise HTTPException(status_code=409, detail="O'quvchi boshqa tashkilotga biriktirilgan")
            other = await conn.fetchval(
                """
                SELECT c.name FROM school_class_students cs JOIN school_classes c ON c.id=cs.class_id
                WHERE cs.student_id=$1 AND cs.left_at IS NULL AND c.center_id=$2 AND c.id<>$3
                """,
                user["id"], staff["center_id"], class_id
            )
            if other:
                raise HTTPException(status_code=409, detail=f"O'quvchi hozir {other} sinfida")
            await conn.execute(
                """
                INSERT INTO school_class_students(class_id, student_id, left_at)
                VALUES ($1, $2, NULL)
                ON CONFLICT(class_id, student_id) DO UPDATE SET left_at=NULL, joined_at=NOW()
                """,
                class_id, user["id"]
            )
            await conn.execute("UPDATE users SET center_id=$1 WHERE id=$2", staff["center_id"], user["id"])
    return {"message": "O'quvchi qo'shildi"}


@router.delete("/classes/{class_id}/students/{student_id}")
async def remove_student(
    class_id: int,
    student_id: int,
    staff: dict = Depends(require_any_permission("manage_students"))
):
    db = await get_pool()
    async with db.acquire() as conn:
        await _accessible_class_or_404(conn, class_id, staff)
        row = await conn.fetchrow(
            """
            UPDATE school_class_students SET left_at=NOW()
            WHERE class_id=$1 AND student_id=$2 AND left_at IS NULL RETURNING id
            """,
            class_id, student_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="O'quvchi sinfda topilmadi")
    return {"message": "O'quvchi chiqarildi"}


@router.get("/results")
async def school_results(staff: dict = Depends(require_any_permission("view_results"))):
    db = await get_pool()
    can_view_all = bool({"manage_classes", "manage_students", "view_reports"}.intersection(staff["permissions"]))
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT er.id, er.section, er.score, er.total, er.writing_band, er.speaking_band,
                   COALESCE(t.title, er.test_slug, 'IELTS Mock SS') AS test_title,
                   er.submitted_at, u.id AS student_id, u.full_name, u.email,
                   c.id AS class_id, c.name AS class_name
            FROM exam_results er
            LEFT JOIN tests t ON t.id=er.test_id
            JOIN users u ON u.email=er.email
            JOIN school_class_students cs ON cs.student_id=u.id AND cs.left_at IS NULL
            JOIN school_classes c ON c.id=cs.class_id
            WHERE c.center_id=$1
              AND ($2::boolean OR EXISTS (
                  SELECT 1 FROM school_teacher_assignments a WHERE a.class_id=c.id AND a.staff_id=$3
              ))
            ORDER BY er.submitted_at DESC LIMIT 500
            """,
            staff["center_id"], can_view_all, staff["staff_id"]
        )
    return [dict(row) | {"submitted_at": row["submitted_at"].isoformat()} for row in rows]
