import hashlib
import hmac
import os
import re
import secrets
import zlib
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

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


def require_admin(x_admin_secret: str = Header("", alias="X-Admin-Secret")):
    secret = os.environ.get("ADMIN_SECRET", "")
    if len(secret) < 32 or not hmac.compare_digest(x_admin_secret, secret):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")


class AdminTestUpdate(BaseModel):
    title: str
    description: str = ""
    test_type: str = "IELTS Academic"
    visibility: str = "public"
    center_id: Optional[int] = None
    duration_minutes: int = 180
    difficulty: str = "Medium"
    attempt_limit: int = 1
    card_order: int = 100


class AdminTestAssignment(BaseModel):
    center_id: int


class TestCardCreate(BaseModel):
    title: str
    description: str = ""
    visibility: str = "public"
    center_id: Optional[int] = None
    card_order: int = 100


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
    if "IELTSMock.submitResult" not in html:
        raise HTTPException(
            status_code=400,
            detail=(f"{filename}: natija protokoli topilmadi. Test yakunida "
                    "window.IELTSMock.submitResult({score, total, answers}) chaqirilsin; "
                    "Writing uchun writing_task1 va writing_task2 ham yuborilsin")
        )
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
        center_id = user.get("center_id") if user else None
        group_id = user.get("group_id") if user else None
        user_id = user["id"] if user else None
        rows = await conn.fetch(
            """
            SELECT t.id, t.slug, t.title, t.description, t.test_type, t.visibility, t.status, t.card_order,
                   t.duration_minutes, t.difficulty, t.attempt_limit, t.legacy_url,
                   c.name AS organization_name, c.brand_name, c.brand_logo_url, c.brand_primary_color,
                   COALESCE((SELECT ARRAY_AGG(s.section ORDER BY s.id)
                             FROM test_html_sections s WHERE s.test_id=t.id),
                            CASE WHEN t.status='planned' OR t.slug='ielts-mock-ss-1'
                                 THEN ARRAY['listening','reading','writing','speaking']::text[]
                                 ELSE ARRAY[]::text[] END) AS sections
            FROM tests t
            LEFT JOIN centers c ON c.id=t.center_id
            WHERE t.status IN ('published','planned') AND (
              t.visibility='public'
              OR ($1::integer IS NOT NULL AND t.visibility='organization' AND t.center_id=$1)
              OR ($1::integer IS NOT NULL AND EXISTS (
                SELECT 1 FROM test_assignments a
                WHERE a.test_id=t.id AND a.center_id=$1
                  AND (a.available_from IS NULL OR a.available_from<=NOW())
                  AND (a.available_until IS NULL OR a.available_until>=NOW())
                  AND ((a.class_id IS NULL AND a.group_id IS NULL)
                       OR a.group_id=$2
                       OR EXISTS (SELECT 1 FROM school_class_students cs
                                  WHERE cs.class_id=a.class_id AND cs.student_id=$3 AND cs.left_at IS NULL))
              ))
            )
            ORDER BY t.card_order, t.created_at DESC
            """,
            center_id, group_id, user_id
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


@router.post("/manage/cards")
async def organization_create_card(data:TestCardCreate,current_user:dict=Depends(get_current_head_teacher)):
    if not data.title.strip(): raise HTTPException(400,"Test nomini kiriting")
    db=await get_pool()
    async with db.acquire() as conn:
        allowed=await conn.fetchval("SELECT test_upload_enabled FROM centers WHERE id=$1",current_user["center_id"])
        if not allowed: raise HTTPException(403,"Super-admin test yuklash vakolatini o'chirgan")
        test_id=await conn.fetchval("""INSERT INTO tests(slug,title,description,test_type,visibility,center_id,status,created_by,card_order)
          VALUES($1,$2,$3,'IELTS Academic','organization',$4,'planned',$5,$6) RETURNING id""",f"org-{current_user['center_id']}-{secrets.token_urlsafe(8).lower()}",data.title.strip(),data.description[:1000],current_user["center_id"],current_user["id"],max(0,min(data.card_order,9999)))
    return {"id":test_id,"status":"planned"}


@router.post("/manage/{test_id}/sections")
async def organization_attach_sections(test_id:int,listening:Optional[UploadFile]=File(None),reading:Optional[UploadFile]=File(None),writing:Optional[UploadFile]=File(None),speaking:Optional[UploadFile]=File(None),current_user:dict=Depends(get_current_head_teacher)):
    files={k:v for k,v in {"listening":listening,"reading":reading,"writing":writing,"speaking":speaking}.items() if v and v.filename}
    if not files: raise HTTPException(400,"Kamida bitta HTML fayl tanlang")
    validated={};total=0
    for section,file in files.items():
        content=await file.read(MAX_HTML_BYTES+1);total+=len(content)
        if total>MAX_FULL_MOCK_BYTES: raise HTTPException(413,"Fayllar 20 MB limitdan oshdi")
        _,compressed,digest=_validate_html_file(file,content);validated[section]=(file.filename,compressed,digest,len(content))
    db=await get_pool()
    async with db.acquire() as conn:
      async with conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM tests WHERE id=$1 AND center_id=$2",test_id,current_user["center_id"]): raise HTTPException(404,"Test topilmadi")
        for section,(name,content,digest,size) in validated.items(): await conn.execute("""INSERT INTO test_html_sections(test_id,section,original_filename,html_compressed,html_sha256,original_size) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(test_id,section) DO UPDATE SET original_filename=EXCLUDED.original_filename,html_compressed=EXCLUDED.html_compressed,html_sha256=EXCLUDED.html_sha256,original_size=EXCLUDED.original_size,created_at=NOW()""",test_id,section,name,content,digest,size)
        await conn.execute("UPDATE tests SET status='draft',updated_at=NOW() WHERE id=$1 AND status='planned'",test_id)
    return {"message":"Fayllar biriktirildi"}


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
        allowed = await conn.fetchval("SELECT test_upload_enabled FROM centers WHERE id=$1", current_user["center_id"])
        if not allowed:
            raise HTTPException(status_code=403, detail="Super-admin test yuklash vakolatini o'chirgan")
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


@router.get("/admin/all")
async def admin_all_tests(_: None = Depends(require_admin)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT t.id,t.slug,t.title,t.description,t.test_type,t.visibility,t.center_id,t.duration_minutes,
                   t.difficulty,t.attempt_limit,t.status,t.card_order,t.legacy_url,t.created_at,c.name AS organization_name,
                   COALESCE(ARRAY_REMOVE(ARRAY_AGG(DISTINCT s.section),NULL),ARRAY[]::text[]) sections,
                   COALESCE(ARRAY_REMOVE(ARRAY_AGG(DISTINCT a.center_id),NULL),ARRAY[]::int[]) assigned_centers
            FROM tests t LEFT JOIN centers c ON c.id=t.center_id
            LEFT JOIN test_html_sections s ON s.test_id=t.id LEFT JOIN test_assignments a ON a.test_id=t.id
            GROUP BY t.id,c.id ORDER BY t.card_order,t.created_at DESC
        """)
    return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/admin/upload")
async def admin_upload_test(
    title: str=Form(...), description: str=Form(""), test_type: str=Form("IELTS Academic"),
    visibility: str=Form("public"), center_id: Optional[int]=Form(None), duration_minutes: int=Form(180),
    difficulty: str=Form("Medium"), attempt_limit: int=Form(1), listening: Optional[UploadFile]=File(None),
    reading: Optional[UploadFile]=File(None), writing: Optional[UploadFile]=File(None),
    speaking: Optional[UploadFile]=File(None), _: None=Depends(require_admin)
):
    if visibility not in ("public","organization") or (visibility == "organization" and not center_id):
        raise HTTPException(400, "Visibility yoki tashkilot noto'g'ri")
    if not title.strip() or not 1 <= duration_minutes <= 360 or not 1 <= attempt_limit <= 20:
        raise HTTPException(400, "Test ma'lumotlari noto'g'ri")
    files={k:v for k,v in {"listening":listening,"reading":reading,"writing":writing,"speaking":speaking}.items() if v and v.filename}
    if not files: raise HTTPException(400,"Kamida bitta HTML section yuklang")
    validated={}; total=0
    for section,file in files.items():
        content=await file.read(MAX_HTML_BYTES+1); total+=len(content)
        if total>MAX_FULL_MOCK_BYTES: raise HTTPException(413,"Full mock hajmi 20 MB dan oshmasligi kerak")
        _,compressed,digest=_validate_html_file(file,content);validated[section]=(file.filename,compressed,digest,len(content))
    db=await get_pool()
    async with db.acquire() as conn:
      async with conn.transaction():
        if center_id and not await conn.fetchval("SELECT 1 FROM centers WHERE id=$1",center_id): raise HTTPException(404,"Tashkilot topilmadi")
        test_id=await conn.fetchval("""INSERT INTO tests(slug,title,description,test_type,visibility,center_id,duration_minutes,difficulty,attempt_limit,status)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'draft') RETURNING id""",f"admin-{secrets.token_urlsafe(8).lower()}",title.strip(),description[:1000],test_type[:80],visibility,center_id,duration_minutes,difficulty[:30],attempt_limit)
        for section,(name,data,digest,size) in validated.items(): await conn.execute("INSERT INTO test_html_sections(test_id,section,original_filename,html_compressed,html_sha256,original_size) VALUES($1,$2,$3,$4,$5,$6)",test_id,section,name,data,digest,size)
    return {"id":test_id,"status":"draft"}


@router.post("/admin/cards")
async def admin_create_card(data: TestCardCreate, _: None = Depends(require_admin)):
    if not data.title.strip() or data.visibility not in ("public", "organization"):
        raise HTTPException(400, "Karta ma'lumotlari noto'g'ri")
    if data.visibility == "organization" and not data.center_id:
        raise HTTPException(400, "Tashkilotni tanlang")
    db = await get_pool()
    async with db.acquire() as conn:
        if data.center_id and not await conn.fetchval("SELECT 1 FROM centers WHERE id=$1", data.center_id):
            raise HTTPException(404, "Tashkilot topilmadi")
        test_id = await conn.fetchval(
            """INSERT INTO tests(slug,title,description,test_type,visibility,center_id,status,card_order)
               VALUES($1,$2,$3,'IELTS Academic',$4,$5,'planned',$6) RETURNING id""",
            f"card-{secrets.token_urlsafe(8).lower()}", data.title.strip(), data.description[:1000],
            data.visibility, data.center_id, max(0, min(data.card_order, 9999))
        )
    return {"id": test_id, "status": "planned"}


@router.post("/admin/{test_id}/sections")
async def admin_attach_sections(
    test_id: int, listening: Optional[UploadFile]=File(None), reading: Optional[UploadFile]=File(None),
    writing: Optional[UploadFile]=File(None), speaking: Optional[UploadFile]=File(None),
    _: None=Depends(require_admin)
):
    files={k:v for k,v in {"listening":listening,"reading":reading,"writing":writing,"speaking":speaking}.items() if v and v.filename}
    if not files: raise HTTPException(400,"Kamida bitta HTML section tanlang")
    validated={}; total=0
    for section,file in files.items():
        content=await file.read(MAX_HTML_BYTES+1);total+=len(content)
        if total>MAX_FULL_MOCK_BYTES: raise HTTPException(413,"Fayllar hajmi 20 MB dan oshmasligi kerak")
        _,compressed,digest=_validate_html_file(file,content);validated[section]=(file.filename,compressed,digest,len(content))
    db=await get_pool()
    async with db.acquire() as conn:
      async with conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM tests WHERE id=$1",test_id): raise HTTPException(404,"Test kartasi topilmadi")
        for section,(name,content,digest,size) in validated.items():
            await conn.execute("""INSERT INTO test_html_sections(test_id,section,original_filename,html_compressed,html_sha256,original_size)
              VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(test_id,section) DO UPDATE SET original_filename=EXCLUDED.original_filename,
              html_compressed=EXCLUDED.html_compressed,html_sha256=EXCLUDED.html_sha256,original_size=EXCLUDED.original_size,created_at=NOW()""",
              test_id,section,name,content,digest,size)
        await conn.execute("UPDATE tests SET updated_at=NOW() WHERE id=$1",test_id)
    return {"message":"Section fayllari biriktirildi","sections":list(validated)}


@router.delete("/admin/{test_id}/sections/{section}")
async def admin_delete_section(test_id:int,section:str,_:None=Depends(require_admin)):
    if section not in SECTIONS: raise HTTPException(404,"Section topilmadi")
    db=await get_pool()
    async with db.acquire() as conn: await conn.execute("DELETE FROM test_html_sections WHERE test_id=$1 AND section=$2",test_id,section)
    return {"message":"Section olib tashlandi"}


@router.put("/admin/{test_id}")
async def admin_update_test(test_id:int,data:AdminTestUpdate,_:None=Depends(require_admin)):
    if data.visibility not in ("public","organization") or (data.visibility=="organization" and not data.center_id): raise HTTPException(400,"Visibility noto'g'ri")
    if not data.title.strip() or not 1 <= data.duration_minutes <= 360 or not 1 <= data.attempt_limit <= 20: raise HTTPException(400,"Test ma'lumotlari noto'g'ri")
    db=await get_pool()
    async with db.acquire() as conn:
        row=await conn.fetchrow("""UPDATE tests SET title=$2,description=$3,test_type=$4,visibility=$5,center_id=$6,duration_minutes=$7,difficulty=$8,attempt_limit=$9,card_order=$10,updated_at=NOW() WHERE id=$1 RETURNING id""",test_id,data.title.strip(),data.description[:1000],data.test_type[:80],data.visibility,data.center_id,data.duration_minutes,data.difficulty[:30],data.attempt_limit,max(0,min(data.card_order,9999)))
    if not row: raise HTTPException(404,"Test topilmadi")
    return {"message":"Test yangilandi"}


@router.post("/admin/{test_id}/status/{status}")
async def admin_test_status(test_id:int,status:str,_:None=Depends(require_admin)):
    if status not in ("planned","draft","published","archived"): raise HTTPException(400,"Status noto'g'ri")
    db=await get_pool()
    async with db.acquire() as conn:
        if status == "published":
            ready = await conn.fetchval("SELECT legacy_url IS NOT NULL OR EXISTS(SELECT 1 FROM test_html_sections WHERE test_id=tests.id) FROM tests WHERE id=$1",test_id)
            if not ready: raise HTTPException(400,"Available qilish uchun kamida bitta section fayli kerak")
        row=await conn.fetchrow("UPDATE tests SET status=$2,updated_at=NOW() WHERE id=$1 RETURNING id",test_id,status)
    if not row: raise HTTPException(404,"Test topilmadi")
    return {"status":status}


@router.post("/admin/{test_id}/assign")
async def admin_assign_test(test_id:int,data:AdminTestAssignment,_:None=Depends(require_admin)):
    db=await get_pool()
    async with db.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM tests WHERE id=$1",test_id): raise HTTPException(404,"Test topilmadi")
        if not await conn.fetchval("SELECT 1 FROM centers WHERE id=$1",data.center_id): raise HTTPException(404,"Tashkilot topilmadi")
        exists=await conn.fetchval("SELECT 1 FROM test_assignments WHERE test_id=$1 AND center_id=$2 AND class_id IS NULL AND group_id IS NULL",test_id,data.center_id)
        if not exists: await conn.execute("INSERT INTO test_assignments(test_id,center_id) VALUES($1,$2)",test_id,data.center_id)
    return {"message":"Test biriktirildi"}


@router.delete("/admin/{test_id}/assign/{center_id}")
async def admin_unassign_test(test_id:int,center_id:int,_:None=Depends(require_admin)):
    db=await get_pool()
    async with db.acquire() as conn: await conn.execute("DELETE FROM test_assignments WHERE test_id=$1 AND center_id=$2",test_id,center_id)
    return {"message":"Biriktirish olib tashlandi"}


@router.delete("/admin/{test_id}")
async def admin_delete_test(test_id:int,_:None=Depends(require_admin)):
    db=await get_pool()
    async with db.acquire() as conn:
        slug=await conn.fetchval("SELECT slug FROM tests WHERE id=$1",test_id)
        if slug in {"ielts-mock-ss-1","ielts-mock-ss-2","ielts-mock-ss-3","cambridge-ielts-21"}:
            result=await conn.execute("UPDATE tests SET status='archived',updated_at=NOW() WHERE id=$1",test_id)
        else:
            result=await conn.execute("DELETE FROM tests WHERE id=$1",test_id)
    if result.endswith("0"): raise HTTPException(404,"Test topilmadi")
    return {"message":"Test o'chirildi"}


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
            UPDATE tests SET status='pending', updated_at=NOW()
            WHERE id=$1 AND center_id=$2 RETURNING id
            """,
            test_id, current_user["center_id"]
        )
    if not row:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return {"message": "Test super-adminga tasdiqlash uchun yuborildi", "status": "pending"}


@router.get("/{test_id}")
async def test_detail(test_id: int, user: Optional[dict] = Depends(get_optional_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, slug, title, description, test_type, duration_minutes, difficulty, attempt_limit, legacy_url FROM tests WHERE id=$1 AND status='published'",
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
