import hashlib
import re
import secrets
import zlib
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from auth import JWT_ALGO, JWT_SECRET, get_current_head_teacher, get_current_user
from db import get_pool


router = APIRouter(prefix="/api/tests", tags=["test-catalog"])
SECTIONS = ("listening", "reading", "writing", "speaking")
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_FULL_MOCK_BYTES = 20 * 1024 * 1024
UNSAFE_HTML_PATTERNS = (
    (re.compile(r"<(?:base|iframe|object|embed)\b", re.I), "base/iframe/object/embed teglari mumkin emas"),
    (re.compile(r"(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//", re.I), "tashqi src/href mumkin emas"),
    (re.compile(r"\b(?:window\.)?top\s*\.\s*location|\bparent\s*\.\s*location", re.I), "yuqori sahifaga navigatsiya mumkin emas"),
    (re.compile(r"document\s*\.\s*cookie|\b(?:localStorage|sessionStorage)\b", re.I), "cookie/localStorage mumkin emas"),
    (re.compile(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(", re.I), "tashqi tarmoq chaqiruvlari mumkin emas"),
)


async def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        token = authorization.split(" ", 1)[1]
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        user_id = int(payload["sub"])
    except Exception:
        return None
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, role, center_id, group_id FROM users WHERE id=$1 AND is_suspended=FALSE", user_id
        )
    return dict(row) if row else None


def _validate_html_file(file: UploadFile, content: bytes) -> tuple[str, bytes, str]:
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".html"):
        raise HTTPException(status_code=400, detail=f"{filename or 'Fayl'} .html formatida bo'lishi kerak")
    if not content or len(content) > MAX_HTML_BYTES:
        raise HTTPException(status_code=400, detail=f"{filename} bo'sh yoki 5 MB limitdan katta")
    try:
        html = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=f"{filename} UTF-8 formatida bo'lishi kerak")
    if "<html" not in html.lower() and "<!doctype" not in html.lower():
        raise HTTPException(status_code=400, detail=f"{filename} to'liq HTML hujjat emas")
    for pattern, message in UNSAFE_HTML_PATTERNS:
        if pattern.search(html):
            raise HTTPException(status_code=400, detail=f"{filename}: {message}")
    digest = hashlib.sha256(content).hexdigest()
    return html, zlib.compress(content, level=9), digest


async def _can_access_test(conn, test_id: int, user: dict) -> bool:
    return bool(await conn.fetchval(
        """
        SELECT 1 FROM tests t
        WHERE t.id=$1 AND t.status='published' AND (
          t.visibility='public'
          OR (t.center_id=$2 AND t.visibility='organization')
          OR EXISTS (
            SELECT 1 FROM test_assignments a
            WHERE a.test_id=t.id AND a.center_id=$2
              AND (a.available_from IS NULL OR a.available_from<=NOW())
              AND (a.available_until IS NULL OR a.available_until>=NOW())
              AND (
                (a.class_id IS NULL AND a.group_id IS NULL)
                OR (a.group_id IS NOT NULL AND a.group_id=$3)
                OR (a.class_id IS NOT NULL AND EXISTS (
                  SELECT 1 FROM school_class_students cs
                  WHERE cs.class_id=a.class_id AND cs.student_id=$4 AND cs.left_at IS NULL
                ))
              )
          )
        )
        """,
        test_id, user.get("center_id"), user.get("group_id"), user["id"]
    ))


@router.get("")
async def public_test_catalog(user: Optional[dict] = Depends(get_optional_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        if user:
            rows = await conn.fetch(
                """
                SELECT DISTINCT t.id, t.slug, t.title, t.description, t.test_type, t.visibility,
                       t.duration_minutes, t.difficulty, t.attempt_limit, t.legacy_url,
                       c.name AS organization_name, c.brand_name, c.brand_logo_url, c.brand_primary_color,
                       COALESCE(ARRAY_REMOVE(ARRAY_AGG(DISTINCT hs.section), NULL), ARRAY[]::text[]) AS sections
                FROM tests t
                LEFT JOIN centers c ON c.id=t.center_id
                LEFT JOIN test_html_sections hs ON hs.test_id=t.id
                LEFT JOIN test_assignments a ON a.test_id=t.id
                WHERE t.status='published' AND (
                  t.visibility='public'
                  OR (t.center_id=$1 AND t.visibility='organization')
                  OR (a.center_id=$1 AND (a.available_from IS NULL OR a.available_from<=NOW())
                      AND (a.available_until IS NULL OR a.available_until>=NOW())
                      AND ((a.class_id IS NULL AND a.group_id IS NULL)
                           OR a.group_id=$2
                           OR EXISTS (SELECT 1 FROM school_class_students cs WHERE cs.class_id=a.class_id AND cs.student_id=$3 AND cs.left_at IS NULL)))
                )
                GROUP BY t.id, c.id ORDER BY t.created_at DESC
                """,
                user.get("center_id"), user.get("group_id"), user["id"]
            )
        else:
            rows = await conn.fetch(
                """
                SELECT t.id, t.slug, t.title, t.description, t.test_type, t.visibility,
                       t.duration_minutes, t.difficulty, t.attempt_limit, t.legacy_url,
                       NULL::text AS organization_name, NULL::text AS brand_name,
                       NULL::text AS brand_logo_url, NULL::text AS brand_primary_color,
                       COALESCE(ARRAY_REMOVE(ARRAY_AGG(DISTINCT hs.section), NULL), ARRAY[]::text[]) AS sections
                FROM tests t LEFT JOIN test_html_sections hs ON hs.test_id=t.id
                WHERE t.status='published' AND t.visibility='public'
                GROUP BY t.id ORDER BY t.created_at DESC
                """
            )
    return [dict(row) for row in rows]


@router.get("/mine")
async def my_tests(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.title, t.description, t.status, t.visibility, t.duration_minutes,
                   t.difficulty, t.created_at,
                   COALESCE(ARRAY_REMOVE(ARRAY_AGG(DISTINCT hs.section), NULL), ARRAY[]::text[]) AS sections
            FROM tests t LEFT JOIN test_html_sections hs ON hs.test_id=t.id
            WHERE t.center_id=$1 GROUP BY t.id ORDER BY t.created_at DESC
            """,
            current_user["center_id"]
        )
    return [dict(row) | {"created_at": row["created_at"].isoformat()} for row in rows]


@router.post("")
async def upload_test(
    title: str = Form(...),
    description: str = Form(""),
    test_type: str = Form("IELTS Academic"),
    duration_minutes: int = Form(180),
    difficulty: str = Form("Medium"),
    attempt_limit: int = Form(1),
    listening: Optional[UploadFile] = File(None),
    reading: Optional[UploadFile] = File(None),
    writing: Optional[UploadFile] = File(None),
    speaking: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_head_teacher)
):
    title = title.strip()
    if not title or len(title) > 160:
        raise HTTPException(status_code=400, detail="Test nomi 1-160 belgi bo'lishi kerak")
    if duration_minutes < 1 or duration_minutes > 360:
        raise HTTPException(status_code=400, detail="Test vaqti 1-360 daqiqa bo'lishi kerak")
    if attempt_limit < 1 or attempt_limit > 20:
        raise HTTPException(status_code=400, detail="Urinish limiti 1-20 oralig'ida bo'lishi kerak")
    uploads = {"listening": listening, "reading": reading, "writing": writing, "speaking": speaking}
    uploads = {section: file for section, file in uploads.items() if file and file.filename}
    if not uploads:
        raise HTTPException(status_code=400, detail="Kamida bitta HTML section yuklang")
    validated = {}
    total_size = 0
    for section, file in uploads.items():
        content = await file.read(MAX_HTML_BYTES + 1)
        total_size += len(content)
        if total_size > MAX_FULL_MOCK_BYTES:
            raise HTTPException(status_code=413, detail="Full mock hajmi 20 MB dan oshmasligi kerak")
        html, compressed, digest = _validate_html_file(file, content)
        validated[section] = (file.filename, compressed, digest, len(content))

    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            slug = f"org-{current_user['center_id']}-{secrets.token_urlsafe(8).lower()}"
            test_id = await conn.fetchval(
                """
                INSERT INTO tests(slug, title, description, test_type, visibility, center_id,
                                  duration_minutes, difficulty, attempt_limit, status, created_by)
                VALUES ($1,$2,$3,$4,'organization',$5,$6,$7,$8,'draft',$9) RETURNING id
                """,
                slug, title, description.strip()[:1000], test_type.strip()[:80], current_user["center_id"],
                duration_minutes, difficulty.strip()[:30], attempt_limit, current_user["id"]
            )
            for section, (filename, compressed, digest, original_size) in validated.items():
                await conn.execute(
                    """
                    INSERT INTO test_html_sections(test_id, section, original_filename, html_compressed, html_sha256, original_size)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    """,
                    test_id, section, filename, compressed, digest, original_size
                )
    return {"id": test_id, "status": "draft", "message": "Test draft sifatida saqlandi"}


@router.post("/{test_id}/publish")
async def publish_test(test_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        section_count = await conn.fetchval(
            "SELECT COUNT(*) FROM test_html_sections WHERE test_id=$1", test_id
        )
        if not section_count:
            raise HTTPException(status_code=400, detail="Testsiz section fayli yo'q")
        row = await conn.fetchrow(
            """
            UPDATE tests SET status='published', updated_at=NOW()
            WHERE id=$1 AND center_id=$2 RETURNING id
            """,
            test_id, current_user["center_id"]
        )
    if not row:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return {"message": "Test publish qilindi"}


@router.get("/{test_id}")
async def test_detail(test_id: int, user: Optional[dict] = Depends(get_optional_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, description, test_type, duration_minutes, difficulty, attempt_limit, legacy_url FROM tests WHERE id=$1 AND status='published'",
            test_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Test topilmadi")
        if user and not await _can_access_test(conn, test_id, user):
            raise HTTPException(status_code=403, detail="Bu test sizga biriktirilmagan")
        if not user:
            public = await conn.fetchval("SELECT 1 FROM tests WHERE id=$1 AND visibility='public'", test_id)
            if not public:
                raise HTTPException(status_code=401, detail="Kirish talab qilinadi")
        sections = await conn.fetch("SELECT section FROM test_html_sections WHERE test_id=$1 ORDER BY id", test_id)
    return dict(row) | {"sections": [item["section"] for item in sections]}


@router.get("/{test_id}/sections/{section}")
async def test_section_content(
    test_id: int,
    section: str,
    current_user: dict = Depends(get_current_user)
):
    if section not in SECTIONS:
        raise HTTPException(status_code=404, detail="Section topilmadi")
    db = await get_pool()
    async with db.acquire() as conn:
        if not await _can_access_test(conn, test_id, current_user):
            raise HTTPException(status_code=403, detail="Bu test sizga biriktirilmagan")
        row = await conn.fetchrow(
            "SELECT html_compressed, html_sha256 FROM test_html_sections WHERE test_id=$1 AND section=$2",
            test_id, section
        )
    if not row:
        raise HTTPException(status_code=404, detail="Section fayli topilmadi")
    try:
        html_bytes = zlib.decompress(bytes(row["html_compressed"]))
        if len(html_bytes) > MAX_HTML_BYTES:
            raise ValueError("HTML limit exceeded")
        html = html_bytes.decode("utf-8-sig")
    except Exception:
        raise HTTPException(status_code=500, detail="Section faylini ochib bo'lmadi")
    return {"html": html, "sha256": row["html_sha256"]}
