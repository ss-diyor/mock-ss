import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import get_current_head_teacher
from db import get_pool


router = APIRouter(prefix="/api/school", tags=["school"])

ALLOWED_PERMISSIONS = {
    "manage_staff", "manage_classes", "manage_students", "view_results",
    "manage_exams", "view_reports", "manage_branding",
}
DEFAULT_POSITIONS = (
    ("Direktor o'rinbosari", ["manage_staff", "manage_classes", "manage_students", "view_results", "view_reports"]),
    ("Sinf rahbari", ["manage_students", "view_results"]),
    ("Fan o'qituvchisi", ["view_results", "manage_exams"]),
    ("Administrator", ["manage_classes", "manage_students"]),
)


class PositionCreateIn(BaseModel):
    name: str
    permissions: list[str] = Field(default_factory=list)


class StaffCreateIn(BaseModel):
    login: str
    position_id: int
    employee_code: Optional[str] = None


class ClassCreateIn(BaseModel):
    name: str
    academic_year: str
    grade_level: Optional[int] = None
    homeroom_staff_id: Optional[int] = None


class ClassStudentIn(BaseModel):
    login: str


async def _school_or_404(conn, current_user: dict, for_update: bool = False):
    suffix = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"SELECT id, name, organization_type, is_active FROM centers WHERE id=$1{suffix}",
        current_user["center_id"]
    )
    if not row or row["organization_type"] != "school":
        raise HTTPException(status_code=404, detail="Maktab topilmadi")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Maktab faol emas")
    return row


async def _ensure_default_positions(conn, center_id: int):
    for name, permissions in DEFAULT_POSITIONS:
        await conn.execute(
            """
            INSERT INTO school_positions(center_id, name, permissions, is_system)
            VALUES ($1, $2, $3::jsonb, TRUE)
            ON CONFLICT(center_id, name) DO NOTHING
            """,
            center_id, name, json.dumps(permissions)
        )


@router.get("/positions")
async def list_positions(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        await _ensure_default_positions(conn, school["id"])
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.permissions, p.is_system, p.created_at,
                   COUNT(s.id) FILTER (WHERE s.is_active=TRUE) AS staff_count
            FROM school_positions p
            LEFT JOIN school_staff s ON s.position_id=p.id
            WHERE p.center_id=$1
            GROUP BY p.id ORDER BY p.is_system DESC, p.name
            """,
            school["id"]
        )
    return [
        dict(r) | {
            "permissions": json.loads(r["permissions"]) if isinstance(r["permissions"], str) else r["permissions"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.post("/positions")
async def create_position(data: PositionCreateIn, current_user: dict = Depends(get_current_head_teacher)):
    name = data.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=400, detail="Lavozim nomi 1-80 belgi bo'lishi kerak")
    permissions = list(dict.fromkeys(data.permissions))
    if any(permission not in ALLOWED_PERMISSIONS for permission in permissions):
        raise HTTPException(status_code=400, detail="Noma'lum ruxsat yuborildi")
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        exists = await conn.fetchval(
            "SELECT 1 FROM school_positions WHERE center_id=$1 AND LOWER(name)=LOWER($2)", school["id"], name
        )
        if exists:
            raise HTTPException(status_code=409, detail="Bu lavozim mavjud")
        row = await conn.fetchrow(
            """
            INSERT INTO school_positions(center_id, name, permissions)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id, name, permissions, is_system, created_at
            """,
            school["id"], name, json.dumps(permissions)
        )
    return dict(row) | {
        "permissions": json.loads(row["permissions"]) if isinstance(row["permissions"], str) else row["permissions"],
        "created_at": row["created_at"].isoformat(), "staff_count": 0,
    }


@router.get("/staff")
async def list_staff(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        rows = await conn.fetch(
            """
            SELECT s.id, s.user_id, s.employee_code, s.is_active, s.created_at,
                   u.full_name, u.username, u.email, p.id AS position_id, p.name AS position_name
            FROM school_staff s
            JOIN users u ON u.id=s.user_id
            LEFT JOIN school_positions p ON p.id=s.position_id
            WHERE s.center_id=$1 ORDER BY s.is_active DESC, u.full_name
            """,
            school["id"]
        )
    return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/staff")
async def add_staff(data: StaffCreateIn, current_user: dict = Depends(get_current_head_teacher)):
    login = data.login.strip().lower().lstrip("@")
    if not login:
        raise HTTPException(status_code=400, detail="Username yoki email kiriting")
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        position = await conn.fetchrow(
            "SELECT id FROM school_positions WHERE id=$1 AND center_id=$2", data.position_id, school["id"]
        )
        if not position:
            raise HTTPException(status_code=404, detail="Lavozim topilmadi")
        user = await conn.fetchrow("SELECT id, center_id FROM users WHERE username=$1 OR email=$1", login)
        if not user:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi; avval ro'yxatdan o'tishi kerak")
        if user["center_id"] and user["center_id"] != school["id"]:
            raise HTTPException(status_code=409, detail="Foydalanuvchi boshqa tashkilotga biriktirilgan")
        duplicate = await conn.fetchval(
            "SELECT 1 FROM school_staff WHERE center_id=$1 AND user_id=$2", school["id"], user["id"]
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Bu xodim allaqachon qo'shilgan")
        row = await conn.fetchrow(
            """
            INSERT INTO school_staff(center_id, user_id, position_id, employee_code)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            school["id"], user["id"], data.position_id,
            data.employee_code.strip()[:40] if data.employee_code else None
        )
        await conn.execute(
            """
            UPDATE users SET center_id=$1,
                role=CASE WHEN role='head_teacher' THEN role ELSE 'school_staff' END
            WHERE id=$2
            """,
            school["id"], user["id"]
        )
    return {"message": "Xodim qo'shildi", "id": row["id"]}


@router.post("/staff/{staff_id}/toggle")
async def toggle_staff(staff_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        row = await conn.fetchrow(
            """
            UPDATE school_staff SET is_active=NOT is_active
            WHERE id=$1 AND center_id=$2 RETURNING is_active
            """,
            staff_id, school["id"]
        )
        if not row:
            raise HTTPException(status_code=404, detail="Xodim topilmadi")
    return {"is_active": row["is_active"]}


@router.get("/classes")
async def list_classes(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        rows = await conn.fetch(
            """
            SELECT c.id, c.name, c.academic_year, c.grade_level, c.is_active, c.created_at,
                   c.homeroom_staff_id, u.full_name AS homeroom_teacher,
                   COUNT(cs.id) FILTER (WHERE cs.left_at IS NULL) AS students_count
            FROM school_classes c
            LEFT JOIN school_staff s ON s.id=c.homeroom_staff_id
            LEFT JOIN users u ON u.id=s.user_id
            LEFT JOIN school_class_students cs ON cs.class_id=c.id
            WHERE c.center_id=$1
            GROUP BY c.id, u.full_name ORDER BY c.academic_year DESC, c.grade_level, c.name
            """,
            school["id"]
        )
    return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/classes")
async def create_class(data: ClassCreateIn, current_user: dict = Depends(get_current_head_teacher)):
    name, academic_year = data.name.strip(), data.academic_year.strip()
    if not name or not academic_year:
        raise HTTPException(status_code=400, detail="Sinf nomi va o'quv yilini kiriting")
    if data.grade_level is not None and not 1 <= data.grade_level <= 12:
        raise HTTPException(status_code=400, detail="Sinf bosqichi 1-12 oralig'ida bo'lishi kerak")
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        if data.homeroom_staff_id:
            own_staff = await conn.fetchval(
                "SELECT 1 FROM school_staff WHERE id=$1 AND center_id=$2 AND is_active=TRUE",
                data.homeroom_staff_id, school["id"]
            )
            if not own_staff:
                raise HTTPException(status_code=404, detail="Sinf rahbari topilmadi")
        duplicate = await conn.fetchval(
            "SELECT 1 FROM school_classes WHERE center_id=$1 AND LOWER(name)=LOWER($2) AND academic_year=$3",
            school["id"], name, academic_year
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Bu sinf shu o'quv yilida mavjud")
        row = await conn.fetchrow(
            """
            INSERT INTO school_classes(center_id, name, academic_year, grade_level, homeroom_staff_id)
            VALUES ($1, $2, $3, $4, $5) RETURNING id, name, academic_year
            """,
            school["id"], name, academic_year, data.grade_level, data.homeroom_staff_id
        )
    return dict(row)


@router.get("/classes/{class_id}/students")
async def list_class_students(class_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        own_class = await conn.fetchval("SELECT 1 FROM school_classes WHERE id=$1 AND center_id=$2", class_id, school["id"])
        if not own_class:
            raise HTTPException(status_code=404, detail="Sinf topilmadi")
        rows = await conn.fetch(
            """
            SELECT u.id, u.full_name, u.username, u.email, cs.joined_at
            FROM school_class_students cs JOIN users u ON u.id=cs.student_id
            WHERE cs.class_id=$1 AND cs.left_at IS NULL ORDER BY u.full_name
            """,
            class_id
        )
    return [dict(r) | {"joined_at": r["joined_at"].isoformat()} for r in rows]


@router.post("/classes/{class_id}/students")
async def add_class_student(class_id: int, data: ClassStudentIn, current_user: dict = Depends(get_current_head_teacher)):
    login = data.login.strip().lower().lstrip("@")
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            school = await _school_or_404(conn, current_user, for_update=True)
            own_class = await conn.fetchval("SELECT 1 FROM school_classes WHERE id=$1 AND center_id=$2", class_id, school["id"])
            if not own_class:
                raise HTTPException(status_code=404, detail="Sinf topilmadi")
            user = await conn.fetchrow("SELECT id, role, center_id FROM users WHERE username=$1 OR email=$1", login)
            if not user:
                raise HTTPException(status_code=404, detail="O'quvchi topilmadi; avval ro'yxatdan o'tishi kerak")
            if user["role"] != "student":
                raise HTTPException(status_code=400, detail="Faqat student hisobini sinfga qo'shish mumkin")
            if user["center_id"] and user["center_id"] != school["id"]:
                raise HTTPException(status_code=409, detail="O'quvchi boshqa tashkilotga biriktirilgan")
            other_class = await conn.fetchval(
                """
                SELECT c.name FROM school_class_students cs
                JOIN school_classes c ON c.id=cs.class_id
                WHERE cs.student_id=$1 AND cs.left_at IS NULL AND c.center_id=$2 AND c.id<>$3
                """,
                user["id"], school["id"], class_id
            )
            if other_class:
                raise HTTPException(status_code=409, detail=f"O'quvchi hozir {other_class} sinfida")
            await conn.execute(
                """
                INSERT INTO school_class_students(class_id, student_id, left_at)
                VALUES ($1, $2, NULL)
                ON CONFLICT(class_id, student_id) DO UPDATE SET left_at=NULL, joined_at=NOW()
                """,
                class_id, user["id"]
            )
            await conn.execute("UPDATE users SET center_id=$1 WHERE id=$2", school["id"], user["id"])
    return {"message": "O'quvchi sinfga qo'shildi"}


@router.delete("/classes/{class_id}/students/{student_id}")
async def remove_class_student(class_id: int, student_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        school = await _school_or_404(conn, current_user)
        row = await conn.fetchrow(
            """
            UPDATE school_class_students cs SET left_at=NOW()
            FROM school_classes c
            WHERE cs.class_id=c.id AND c.id=$1 AND c.center_id=$2
              AND cs.student_id=$3 AND cs.left_at IS NULL
            RETURNING cs.id
            """,
            class_id, school["id"], student_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="O'quvchi sinfda topilmadi")
    return {"message": "O'quvchi sinfdan chiqarildi"}
