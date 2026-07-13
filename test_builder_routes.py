import base64
import html
import json
import secrets
import os
import hmac
from typing import Any, Optional

import jwt
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from auth import JWT_ALGO, JWT_SECRET
from db import get_pool


router = APIRouter(prefix="/api/test-builder", tags=["test-builder"])
SECTIONS = ("listening", "reading", "writing", "speaking")
QUESTION_TYPES = {"single_choice", "multiple_choice", "short_answer", "true_false", "writing_task", "speaking_prompt"}
MEDIA_LIMITS = {"image": 5 * 1024 * 1024, "audio": 20 * 1024 * 1024}
MEDIA_MIMES = {
    "image": {"image/jpeg", "image/png", "image/webp", "image/gif"},
    "audio": {"audio/mpeg", "audio/ogg", "audio/webm", "audio/mp4", "audio/wav", "audio/x-wav"},
}


async def get_current_head_teacher(
    authorization: Optional[str] = Header(None),
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret"),
):
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if x_admin_secret and len(admin_secret) >= 32 and hmac.compare_digest(x_admin_secret, admin_secret):
        return {"id": None, "center_id": None, "role": "admin", "is_admin": True}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Kirish talab qilinadi")
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=[JWT_ALGO])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Token noto'g'ri")
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT id,center_id,role FROM users WHERE id=$1 AND is_suspended=FALSE", user_id)
    if not row or row["role"] != "head_teacher" or not row["center_id"]:
        raise HTTPException(status_code=403, detail="Faqat super-admin yoki tashkilot rahbari uchun")
    return dict(row) | {"is_admin": False}


class BuilderTestIn(BaseModel):
    title: str
    description: str = ""
    test_type: str = "IELTS Academic"
    duration_minutes: int = 180
    attempt_limit: int = 1


class BuilderSectionIn(BaseModel):
    section: str
    title: str
    instructions: str = ""
    passage: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class BuilderQuestionIn(BaseModel):
    question_type: str
    prompt: str
    options: list[Any] = Field(default_factory=list)
    correct_answer: Any = None
    points: float = 1
    explanation: str = ""
    media_id: Optional[int] = None


class ReorderIn(BaseModel):
    question_ids: list[int]


async def _owned_test(conn, test_id: int, user: dict, editable: bool = False):
    status_filter = " AND status IN ('draft','planned')" if editable else ""
    if user.get("is_admin"):
        row = await conn.fetchrow(f"SELECT id,title,description,test_type,duration_minutes,attempt_limit,status,center_id FROM tests WHERE id=$1{status_filter}", test_id)
    else:
        row = await conn.fetchrow(
            f"SELECT id,title,description,test_type,duration_minutes,attempt_limit,status,center_id FROM tests WHERE id=$1 AND center_id=$2{status_filter}",
            test_id, user["center_id"]
        )
    if not row:
        raise HTTPException(status_code=404, detail="Test topilmadi yoki tahrirlashga yopilgan")
    return row


def _validate_question(data: BuilderQuestionIn):
    if data.question_type not in QUESTION_TYPES:
        raise HTTPException(status_code=400, detail="Savol turi qo'llab-quvvatlanmaydi")
    if not data.prompt.strip() or len(data.prompt) > 10_000:
        raise HTTPException(status_code=400, detail="Savol matni noto'g'ri")
    if not 0 < data.points <= 100:
        raise HTTPException(status_code=400, detail="Ball 0–100 oralig'ida bo'lishi kerak")
    if data.question_type in {"single_choice", "multiple_choice", "short_answer", "true_false"} and not float(data.points).is_integer():
        raise HTTPException(status_code=400, detail="Avtomatik tekshiriladigan savol balli butun son bo'lishi kerak")
    if data.question_type in {"single_choice", "multiple_choice"} and len(data.options) < 2:
        raise HTTPException(status_code=400, detail="Kamida 2 ta variant kiriting")
    if data.question_type in {"single_choice", "multiple_choice", "short_answer", "true_false"} and data.correct_answer in (None, "", []):
        raise HTTPException(status_code=400, detail="To'g'ri javobni kiriting")
    option_values = {str(value).strip() for value in data.options}
    if data.question_type == "single_choice" and str(data.correct_answer).strip() not in option_values:
        raise HTTPException(status_code=400, detail="To'g'ri javob variantlar ichida bo'lishi kerak")
    if data.question_type == "multiple_choice" and (
        not isinstance(data.correct_answer, list) or not set(map(str, data.correct_answer)).issubset(option_values)
    ):
        raise HTTPException(status_code=400, detail="Multiple choice javoblari variantlarga mos emas")
    if len(json.dumps(data.options, ensure_ascii=False)) > 50_000:
        raise HTTPException(status_code=413, detail="Variantlar hajmi juda katta")


@router.get("/tests")
async def builder_tests(current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id,t.title,t.description,t.status,t.duration_minutes,t.attempt_limit,t.created_at,
                   COUNT(DISTINCT s.id) AS section_count,COUNT(DISTINCT q.id) AS question_count
            FROM tests t LEFT JOIN test_builder_sections s ON s.test_id=t.id
            LEFT JOIN test_builder_questions q ON q.section_id=s.id
            WHERE ($1::integer IS NULL OR t.center_id=$1) AND EXISTS(SELECT 1 FROM test_builder_sections bs WHERE bs.test_id=t.id)
            GROUP BY t.id ORDER BY t.created_at DESC
            """, None if current_user.get("is_admin") else current_user["center_id"]
        )
    return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/tests")
async def create_builder_test(data: BuilderTestIn, current_user: dict = Depends(get_current_head_teacher)):
    title = data.title.strip()
    if not title or len(title) > 160 or not 1 <= data.duration_minutes <= 360 or not 1 <= data.attempt_limit <= 20:
        raise HTTPException(status_code=400, detail="Test ma'lumotlari noto'g'ri")
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            test_id = await conn.fetchval(
                """
                INSERT INTO tests(slug,title,description,test_type,visibility,center_id,duration_minutes,attempt_limit,status,created_by)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,'draft',$9) RETURNING id
                """, f"builder-{current_user.get('center_id') or 'admin'}-{secrets.token_urlsafe(8).lower()}", title,
                data.description.strip()[:1000], data.test_type.strip()[:80],
                "public" if current_user.get("is_admin") else "organization", current_user.get("center_id"),
                data.duration_minutes, data.attempt_limit, current_user["id"]
            )
            for order, section in enumerate(SECTIONS):
                await conn.execute(
                    "INSERT INTO test_builder_sections(test_id,section,title,sort_order) VALUES($1,$2,$3,$4)",
                    test_id, section, section.title(), order
                )
    return {"id": test_id, "message": "Test konstruktori yaratildi"}


@router.get("/tests/{test_id}")
async def builder_test_detail(test_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db = await get_pool()
    async with db.acquire() as conn:
        test = await _owned_test(conn, test_id, current_user)
        sections = await conn.fetch(
            "SELECT id,section,title,instructions,passage,sort_order,settings FROM test_builder_sections WHERE test_id=$1 ORDER BY sort_order,id", test_id
        )
        questions = await conn.fetch(
            """
            SELECT q.id,q.section_id,q.question_type,q.prompt,q.options,q.correct_answer,q.points,q.explanation,q.media_id,q.sort_order
            FROM test_builder_questions q JOIN test_builder_sections s ON s.id=q.section_id
            WHERE s.test_id=$1 ORDER BY q.section_id,q.sort_order,q.id
            """, test_id
        )
        media = await conn.fetch("SELECT id,kind,original_filename,mime_type,file_size,created_at FROM test_builder_media WHERE test_id=$1 ORDER BY created_at", test_id)
    def decoded(value, fallback):
        if value is None: return fallback
        if isinstance(value, str):
            try: return json.loads(value)
            except json.JSONDecodeError: return fallback
        return value
    payload = dict(test)
    payload["sections"] = []
    for s in sections:
        section_payload = dict(s)
        section_payload["settings"] = decoded(section_payload.get("settings"), {})
        section_payload["questions"] = []
        for q in questions:
            if q["section_id"] != s["id"]: continue
            question_payload = dict(q)
            question_payload["options"] = decoded(question_payload.get("options"), [])
            question_payload["correct_answer"] = decoded(question_payload.get("correct_answer"), None)
            question_payload["points"] = float(question_payload["points"])
            section_payload["questions"].append(question_payload)
        payload["sections"].append(section_payload)
    payload["media"] = [dict(m) | {"created_at": m["created_at"].isoformat()} for m in media]
    return payload


@router.put("/tests/{test_id}")
async def update_builder_test(test_id: int, data: BuilderTestIn, current_user: dict = Depends(get_current_head_teacher)):
    if not data.title.strip() or not 1 <= data.duration_minutes <= 360 or not 1 <= data.attempt_limit <= 20:
        raise HTTPException(status_code=400, detail="Test ma'lumotlari noto'g'ri")
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user,editable=True)
        await conn.execute("UPDATE tests SET title=$1,description=$2,test_type=$3,duration_minutes=$4,attempt_limit=$5,updated_at=NOW() WHERE id=$6",data.title.strip()[:160],data.description.strip()[:1000],data.test_type.strip()[:80],data.duration_minutes,data.attempt_limit,test_id)
    return {"message":"Test ma'lumotlari saqlandi"}


@router.put("/tests/{test_id}/sections/{section}")
async def update_builder_section(test_id: int, section: str, data: BuilderSectionIn, current_user: dict = Depends(get_current_head_teacher)):
    if section not in SECTIONS or data.section != section:
        raise HTTPException(status_code=400, detail="Bo'lim noto'g'ri")
    db = await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn, test_id, current_user, editable=True)
        row = await conn.fetchrow(
            """
            UPDATE test_builder_sections SET title=$1,instructions=$2,passage=$3,settings=$4::jsonb,updated_at=NOW()
            WHERE test_id=$5 AND section=$6 RETURNING id
            """, data.title.strip()[:160] or section.title(), data.instructions[:10_000], data.passage[:100_000],
            json.dumps(data.settings), test_id, section
        )
    return {"id": row["id"], "message": "Bo'lim saqlandi"}


@router.post("/tests/{test_id}/sections/{section}/questions")
async def create_builder_question(test_id: int, section: str, data: BuilderQuestionIn, current_user: dict = Depends(get_current_head_teacher)):
    _validate_question(data)
    if data.question_type == "writing_task" and section != "writing" or data.question_type == "speaking_prompt" and section != "speaking":
        raise HTTPException(status_code=400, detail="Savol turi bo'limga mos emas")
    db = await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn, test_id, current_user, editable=True)
        question_section = await conn.fetchval("SELECT s.section FROM test_builder_questions q JOIN test_builder_sections s ON s.id=q.section_id WHERE q.id=$1 AND s.test_id=$2", question_id, test_id)
        if not question_section: raise HTTPException(status_code=404, detail="Savol topilmadi")
        if data.question_type == "writing_task" and question_section != "writing" or data.question_type == "speaking_prompt" and question_section != "speaking":
            raise HTTPException(status_code=400, detail="Savol turi bo'limga mos emas")
        section_id = await conn.fetchval("SELECT id FROM test_builder_sections WHERE test_id=$1 AND section=$2", test_id, section)
        if not section_id: raise HTTPException(status_code=404, detail="Bo'lim topilmadi")
        if await conn.fetchval("SELECT COUNT(*) FROM test_builder_questions WHERE section_id=$1", section_id) >= 200:
            raise HTTPException(status_code=409, detail="Bir bo'limda ko'pi bilan 200 savol bo'lishi mumkin")
        if data.media_id and not await conn.fetchval("SELECT 1 FROM test_builder_media WHERE id=$1 AND test_id=$2", data.media_id, test_id):
            raise HTTPException(status_code=404, detail="Media topilmadi")
        order = await conn.fetchval("SELECT COALESCE(MAX(sort_order),-1)+1 FROM test_builder_questions WHERE section_id=$1", section_id)
        question_id = await conn.fetchval(
            """INSERT INTO test_builder_questions(section_id,question_type,prompt,options,correct_answer,points,explanation,media_id,sort_order)
               VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9) RETURNING id""",
            section_id,data.question_type,data.prompt.strip(),json.dumps(data.options,ensure_ascii=False),
            json.dumps(data.correct_answer,ensure_ascii=False) if data.correct_answer is not None else None,
            data.points,data.explanation[:5000],data.media_id,order
        )
    return {"id": question_id, "message": "Savol qo'shildi"}


@router.put("/tests/{test_id}/questions/{question_id}")
async def update_builder_question(test_id: int, question_id: int, data: BuilderQuestionIn, current_user: dict = Depends(get_current_head_teacher)):
    _validate_question(data)
    db = await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn, test_id, current_user, editable=True)
        if data.media_id and not await conn.fetchval("SELECT 1 FROM test_builder_media WHERE id=$1 AND test_id=$2", data.media_id, test_id):
            raise HTTPException(status_code=404, detail="Media topilmadi")
        row = await conn.fetchrow(
            """
            UPDATE test_builder_questions q SET question_type=$1,prompt=$2,options=$3::jsonb,correct_answer=$4::jsonb,
              points=$5,explanation=$6,media_id=$7,updated_at=NOW()
            FROM test_builder_sections s WHERE q.id=$8 AND q.section_id=s.id AND s.test_id=$9 RETURNING q.id
            """,data.question_type,data.prompt.strip(),json.dumps(data.options,ensure_ascii=False),
            json.dumps(data.correct_answer,ensure_ascii=False) if data.correct_answer is not None else None,
            data.points,data.explanation[:5000],data.media_id,question_id,test_id
        )
    if not row: raise HTTPException(status_code=404, detail="Savol topilmadi")
    return {"message": "Savol yangilandi"}


@router.delete("/tests/{test_id}/questions/{question_id}")
async def delete_builder_question(test_id: int, question_id: int, current_user: dict = Depends(get_current_head_teacher)):
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user,editable=True)
        row=await conn.fetchrow("DELETE FROM test_builder_questions q USING test_builder_sections s WHERE q.id=$1 AND q.section_id=s.id AND s.test_id=$2 RETURNING q.id",question_id,test_id)
    if not row: raise HTTPException(status_code=404,detail="Savol topilmadi")
    return {"message":"Savol o'chirildi"}


@router.post("/tests/{test_id}/sections/{section}/reorder")
async def reorder_builder_questions(test_id: int, section: str, data: ReorderIn, current_user: dict = Depends(get_current_head_teacher)):
    if len(data.question_ids) != len(set(data.question_ids)): raise HTTPException(400,"Savol ID takrorlangan")
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user,editable=True)
        section_id=await conn.fetchval("SELECT id FROM test_builder_sections WHERE test_id=$1 AND section=$2",test_id,section)
        actual=await conn.fetch("SELECT id FROM test_builder_questions WHERE section_id=$1",section_id)
        if {r['id'] for r in actual} != set(data.question_ids): raise HTTPException(400,"Savollar ro'yxati mos emas")
        async with conn.transaction():
            for order,qid in enumerate(data.question_ids): await conn.execute("UPDATE test_builder_questions SET sort_order=$1 WHERE id=$2",order,qid)
    return {"message":"Tartib saqlandi"}


@router.post("/tests/{test_id}/media")
async def upload_builder_media(test_id: int, kind: str = Form(...), file: UploadFile = File(...), current_user: dict = Depends(get_current_head_teacher)):
    if kind not in MEDIA_LIMITS: raise HTTPException(400,"Media turi noto'g'ri")
    mime=(file.content_type or "").lower()
    if mime not in MEDIA_MIMES[kind]: raise HTTPException(400,"Media formati qo'llab-quvvatlanmaydi")
    content=await file.read(MEDIA_LIMITS[kind]+1)
    if not content or len(content)>MEDIA_LIMITS[kind]: raise HTTPException(413,"Media hajmi limitdan oshdi")
    valid = (kind=="image" and (content.startswith((b"\xff\xd8",b"\x89PNG",b"GIF8")) or (content.startswith(b"RIFF") and content[8:12]==b"WEBP"))) or (kind=="audio" and (content.startswith((b"OggS",b"ID3",b"\x1aE\xdf\xa3")) or (content.startswith(b"RIFF") and content[8:12]==b"WAVE") or b"ftyp" in content[:32] or content[:2] in {b"\xff\xfb",b"\xff\xf3",b"\xff\xf2"}))
    if not valid: raise HTTPException(400,"Media fayl tarkibi noto'g'ri")
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user,editable=True)
        used=await conn.fetchval("SELECT COALESCE(SUM(file_size),0) FROM test_builder_media WHERE test_id=$1",test_id)
        if used+len(content)>40*1024*1024: raise HTTPException(413,"Test medialari jami 40 MB dan oshmasligi kerak")
        media_id=await conn.fetchval("INSERT INTO test_builder_media(test_id,kind,original_filename,mime_type,file_data,file_size) VALUES($1,$2,$3,$4,$5,$6) RETURNING id",test_id,kind,(file.filename or kind)[:180],mime,content,len(content))
    return {"id":media_id,"message":"Media yuklandi"}


@router.delete("/tests/{test_id}/media/{media_id}")
async def delete_builder_media(test_id:int,media_id:int,current_user:dict=Depends(get_current_head_teacher)):
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user,editable=True)
        row=await conn.fetchrow("DELETE FROM test_builder_media WHERE id=$1 AND test_id=$2 RETURNING id",media_id,test_id)
    if not row: raise HTTPException(404,"Media topilmadi")
    return {"message":"Media o'chirildi"}


@router.post("/tests/{test_id}/duplicate")
async def duplicate_builder_test(test_id:int,current_user:dict=Depends(get_current_head_teacher)):
    db=await get_pool()
    async with db.acquire() as conn:
      async with conn.transaction():
        source=await _owned_test(conn,test_id,current_user)
        new_id=await conn.fetchval("""INSERT INTO tests(slug,title,description,test_type,visibility,center_id,duration_minutes,difficulty,attempt_limit,status,created_by)
          SELECT $1,title||' — nusxa',description,test_type,visibility,center_id,duration_minutes,difficulty,attempt_limit,'draft',$2 FROM tests WHERE id=$3 RETURNING id""",f"builder-{current_user.get('center_id') or 'admin'}-{secrets.token_urlsafe(8).lower()}",current_user["id"],test_id)
        section_map={}
        for s in await conn.fetch("SELECT * FROM test_builder_sections WHERE test_id=$1 ORDER BY sort_order",test_id):
            section_map[s["id"]]=await conn.fetchval("INSERT INTO test_builder_sections(test_id,section,title,instructions,passage,sort_order,settings) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id",new_id,s["section"],s["title"],s["instructions"],s["passage"],s["sort_order"],s["settings"])
        media_map={}
        for m in await conn.fetch("SELECT * FROM test_builder_media WHERE test_id=$1",test_id):
            media_map[m["id"]]=await conn.fetchval("INSERT INTO test_builder_media(test_id,kind,original_filename,mime_type,file_data,file_size) VALUES($1,$2,$3,$4,$5,$6) RETURNING id",new_id,m["kind"],m["original_filename"],m["mime_type"],m["file_data"],m["file_size"])
        for q in await conn.fetch("SELECT q.* FROM test_builder_questions q JOIN test_builder_sections s ON s.id=q.section_id WHERE s.test_id=$1",test_id):
            await conn.execute("INSERT INTO test_builder_questions(section_id,question_type,prompt,options,correct_answer,points,explanation,media_id,sort_order) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",section_map[q["section_id"]],q["question_type"],q["prompt"],q["options"],q["correct_answer"],q["points"],q["explanation"],media_map.get(q["media_id"]),q["sort_order"])
    return {"id":new_id,"message":f"{source['title']} nusxalandi"}


async def render_builder_section(conn, test_id: int, section: str) -> Optional[str]:
    sec=await conn.fetchrow("SELECT id,title,instructions,passage FROM test_builder_sections WHERE test_id=$1 AND section=$2",test_id,section)
    if not sec: return None
    questions=await conn.fetch("""SELECT q.*,m.kind,m.mime_type,m.file_data FROM test_builder_questions q LEFT JOIN test_builder_media m ON m.id=q.media_id WHERE q.section_id=$1 ORDER BY q.sort_order,q.id""",sec["id"])
    if not questions: return None
    items=[]; answer_key={}; points={}
    for index,q in enumerate(questions,1):
        qid=str(q["id"]); qtype=q["question_type"]; opts=q["options"] if isinstance(q["options"],list) else json.loads(q["options"] or "[]")
        expected=q["correct_answer"]
        if isinstance(expected,str):
            try: expected=json.loads(expected)
            except json.JSONDecodeError: pass
        media=""
        if q["file_data"]:
            uri=f"data:{q['mime_type']};base64,{base64.b64encode(bytes(q['file_data'])).decode()}"
            media=f'<audio controls src="{uri}"></audio>' if q["kind"]=="audio" else f'<img src="{uri}" alt="Savol rasmi">'
        prompt=html.escape(q["prompt"])
        if qtype in {"single_choice","true_false"}:
            values=opts or (["True","False"] if qtype=="true_false" else [])
            control="".join(f'<label class="opt"><input type="radio" name="q{qid}" value="{html.escape(str(v))}"> {html.escape(str(v))}</label>' for v in values)
        elif qtype=="multiple_choice":
            control="".join(f'<label class="opt"><input type="checkbox" name="q{qid}" value="{html.escape(str(v))}"> {html.escape(str(v))}</label>' for v in opts)
        elif qtype=="writing_task": control=f'<textarea name="q{qid}" rows="14" placeholder="Javobingizni yozing..."></textarea>'
        elif qtype=="speaking_prompt": control=f'<textarea name="q{qid}" rows="4" placeholder="Qisqa reja yoki eslatma..."></textarea>'
        else: control=f'<input class="answer" name="q{qid}" placeholder="Javob">'
        items.append(f'<article class="question" data-id="{qid}" data-type="{qtype}"><h3>{index}. {prompt}</h3>{media}{control}</article>')
        # Answer key HTML ichiga chiqarilmaydi; scoring /api/submit da server tomonda bajariladi.
        answer_key[qid]=None;points[qid]=float(q["points"])
    data=json.dumps({"answers":answer_key,"points":points,"section":section},ensure_ascii=False).replace("<","\\u003c")
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{{--b:#1a56e8;--t:#0b1733;--m:#667085;--l:#d9e2f5;--bg:#f5f8ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--t);font-family:Inter,Arial,sans-serif}}main{{max-width:920px;margin:0 auto;padding:28px 18px 90px}}h1{{margin:0 0 8px}}.intro,.passage,.question{{background:#fff;border:1px solid var(--l);border-radius:12px;padding:18px;margin:14px 0}}.passage{{white-space:pre-wrap;line-height:1.65}}.question h3{{font-size:16px}}.opt{{display:block;padding:9px;border:1px solid var(--l);border-radius:7px;margin:7px 0}}input.answer,textarea{{width:100%;padding:11px;border:1px solid var(--l);border-radius:7px;font:inherit}}img{{max-width:100%;max-height:420px;object-fit:contain}}audio{{width:100%;margin:10px 0}}button{{position:fixed;right:22px;bottom:22px;border:0;border-radius:8px;background:var(--b);color:#fff;padding:13px 22px;font-weight:700;cursor:pointer}}</style></head><body><main><section class="intro"><h1>{html.escape(sec['title'])}</h1><p>{html.escape(sec['instructions'] or '')}</p></section>{f'<section class="passage">{html.escape(sec["passage"])}</section>' if sec['passage'] else ''}{''.join(items)}<button onclick="finish()">Yakunlash</button></main><script>const DATA={data};function norm(v){{return String(v??'').trim().toLowerCase()}}function finish(){{const answers={{}};let score=0,total=0;document.querySelectorAll('.question').forEach(q=>{{const id=q.dataset.id,type=q.dataset.type,els=[...q.querySelectorAll('input,textarea')];let value;if(type==='multiple_choice')value=els.filter(x=>x.checked).map(x=>x.value);else if(type==='single_choice'||type==='true_false')value=els.find(x=>x.checked)?.value||'';else value=els[0]?.value||'';answers[id]=value;const expected=DATA.answers[id],pts=Number(DATA.points[id]||1);if(!['writing_task','speaking_prompt'].includes(type)){{total+=pts;if(Array.isArray(expected)){{const a=[...(Array.isArray(value)?value:[value])].map(norm).sort(),b=[...expected].map(norm).sort();if(JSON.stringify(a)===JSON.stringify(b))score+=pts}}else if(norm(value)===norm(expected))score+=pts}}}});const writing=[...document.querySelectorAll('[data-type="writing_task"] textarea')].map(x=>x.value);window.IELTSMock.submitResult({{score:DATA.section==='writing'||DATA.section==='speaking'?null:score,total:DATA.section==='writing'||DATA.section==='speaking'?null:total,answers,writing_task1:writing[0]||null,writing_task2:writing[1]||null}})}}<\/script></body></html>'''


@router.get("/tests/{test_id}/preview/{section}")
async def preview_builder_section(test_id:int,section:str,current_user:dict=Depends(get_current_head_teacher)):
    db=await get_pool()
    async with db.acquire() as conn:
        await _owned_test(conn,test_id,current_user)
        rendered=await render_builder_section(conn,test_id,section)
    if not rendered: raise HTTPException(404,"Preview uchun savol yo'q")
    return {"html":rendered}
