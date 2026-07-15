"""In-app support tickets for guests, authenticated users, and super-admins."""

from __future__ import annotations

import hmac
import html
import os
import re
from typing import Optional

import jwt
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from auth import JWT_ALGO, JWT_SECRET, send_email
from db import get_pool
from notification_center import create_notification, notify_admin


router = APIRouter(tags=["support-center"])
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")
MIN_ADMIN_SECRET_LENGTH = 32
GUEST_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{24,80}$")
CATEGORIES = {"technical", "tests", "results", "billing", "organizations", "other"}
STATUSES = {"open", "waiting_admin", "waiting_user", "resolved", "closed"}


class TicketCreateIn(BaseModel):
    category: str
    message: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None


class MessageCreateIn(BaseModel):
    message: str


class TicketStatusIn(BaseModel):
    status: str


async def ensure_support_tables(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            guest_key TEXT,
            contact_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting_admin',
            priority TEXT NOT NULL DEFAULT 'normal',
            last_message_at TIMESTAMP NOT NULL DEFAULT NOW(),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CHECK (user_id IS NOT NULL OR guest_key IS NOT NULL)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS support_messages (
            id BIGSERIAL PRIMARY KEY,
            ticket_id BIGINT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
            sender_type TEXT NOT NULL CHECK(sender_type IN ('user','admin','system')),
            sender_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            body TEXT NOT NULL,
            read_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS support_tickets_user_idx ON support_tickets(user_id, last_message_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS support_tickets_guest_idx ON support_tickets(guest_key, last_message_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS support_messages_ticket_idx ON support_messages(ticket_id, created_at)"
    )


def _clean(value: Optional[str], limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _message(value: str) -> str:
    value = str(value or "").strip()
    if not 2 <= len(value) <= 3000:
        raise HTTPException(status_code=400, detail="Xabar 2–3000 belgi oralig'ida bo'lishi kerak")
    return value


def _check_admin(secret: str) -> None:
    if not ADMIN_SECRET or ADMIN_SECRET == "admin123" or len(ADMIN_SECRET) < MIN_ADMIN_SECRET_LENGTH:
        raise HTTPException(status_code=503, detail="ADMIN_SECRET sozlanmagan")
    if not hmac.compare_digest(secret, ADMIN_SECRET):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")


async def _support_actor(conn, authorization: Optional[str], guest_key: Optional[str]) -> dict:
    if authorization and authorization.startswith("Bearer "):
        try:
            payload = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=[JWT_ALGO])
            user_id = int(payload["sub"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Sessiya yaroqsiz")
        user = await conn.fetchrow(
            "SELECT id,full_name,email,is_suspended FROM users WHERE id=$1 AND deleted_at IS NULL",
            user_id,
        )
        if not user or user["is_suspended"]:
            raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi")
        return {"user_id": user["id"], "key": user["id"], "name": user["full_name"], "email": user["email"]}
    guest_key = str(guest_key or "").strip()
    if not GUEST_KEY_RE.fullmatch(guest_key):
        raise HTTPException(status_code=400, detail="Yordam markazi identifikatori yaroqsiz")
    return {"user_id": None, "key": guest_key, "name": None, "email": None}


def _ownership(actor: dict, start: int = 2) -> tuple[str, object]:
    if actor["user_id"] is not None:
        return f"t.user_id=${start}", actor["user_id"]
    return f"t.guest_key=${start}", actor["key"]


def _ticket_payload(row) -> dict:
    return {
        "id": row["id"],
        "contact_name": row["contact_name"],
        "category": row["category"],
        "status": row["status"],
        "priority": row["priority"],
        "last_message": row.get("last_message") if hasattr(row, "get") else None,
        "unread_count": row.get("unread_count", 0) if hasattr(row, "get") else 0,
        "last_message_at": row["last_message_at"].isoformat(),
        "created_at": row["created_at"].isoformat(),
    }


@router.post("/api/support/tickets")
async def create_support_ticket(
    data: TicketCreateIn,
    authorization: Optional[str] = Header(None),
    x_support_key: Optional[str] = Header(None, alias="X-Support-Key"),
):
    category = data.category if data.category in CATEGORIES else "other"
    body = _message(data.message)
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            actor = await _support_actor(conn, authorization, x_support_key)
            name = actor["name"] or _clean(data.contact_name, 120)
            email = actor["email"] or _clean(data.contact_email, 180).lower()
            if len(name) < 2 or "@" not in email:
                raise HTTPException(status_code=400, detail="Ism va to'g'ri email kiriting")
            if actor["user_id"] is None:
                recent = await conn.fetchval(
                    "SELECT COUNT(*) FROM support_tickets WHERE guest_key=$1 AND created_at>NOW()-INTERVAL '24 hours'",
                    actor["key"],
                )
                if recent >= 5:
                    raise HTTPException(status_code=429, detail="Bir kunda 5 tagacha murojaat yuborish mumkin")
            ticket_id = await conn.fetchval(
                """
                INSERT INTO support_tickets(user_id,guest_key,contact_name,contact_email,category)
                VALUES($1,$2,$3,$4,$5) RETURNING id
                """,
                actor["user_id"], None if actor["user_id"] else actor["key"], name, email, category,
            )
            await conn.execute(
                "INSERT INTO support_messages(ticket_id,sender_type,sender_user_id,body) VALUES($1,'user',$2,$3)",
                ticket_id, actor["user_id"], body,
            )
            await notify_admin(
                conn,
                "Yangi yordam murojaati",
                f"{name} yangi murojaat yubordi: {body[:120]}",
                kind="task",
                action_url=f"/admin?section=support&ticket={ticket_id}",
                metadata={"event": "support_ticket", "ticket_id": ticket_id},
            )
    return {"id": ticket_id, "status": "waiting_admin"}


@router.get("/api/support/tickets")
async def list_my_support_tickets(
    authorization: Optional[str] = Header(None),
    x_support_key: Optional[str] = Header(None, alias="X-Support-Key"),
):
    db = await get_pool()
    async with db.acquire() as conn:
        actor = await _support_actor(conn, authorization, x_support_key)
        clause, value = _ownership(actor, 1)
        rows = await conn.fetch(
            f"""
            SELECT t.*,
                   (SELECT body FROM support_messages WHERE ticket_id=t.id ORDER BY created_at DESC LIMIT 1) AS last_message,
                   (SELECT COUNT(*) FROM support_messages WHERE ticket_id=t.id AND sender_type='admin' AND read_at IS NULL) AS unread_count
            FROM support_tickets t WHERE {clause}
            ORDER BY t.last_message_at DESC LIMIT 30
            """,
            value,
        )
    return [_ticket_payload(row) for row in rows]


@router.get("/api/support/tickets/{ticket_id}")
async def my_support_ticket(
    ticket_id: int,
    authorization: Optional[str] = Header(None),
    x_support_key: Optional[str] = Header(None, alias="X-Support-Key"),
):
    db = await get_pool()
    async with db.acquire() as conn:
        actor = await _support_actor(conn, authorization, x_support_key)
        clause, value = _ownership(actor)
        ticket = await conn.fetchrow(f"SELECT t.* FROM support_tickets t WHERE t.id=$1 AND {clause}", ticket_id, value)
        if not ticket:
            raise HTTPException(status_code=404, detail="Murojaat topilmadi")
        await conn.execute(
            "UPDATE support_messages SET read_at=COALESCE(read_at,NOW()) WHERE ticket_id=$1 AND sender_type='admin'",
            ticket_id,
        )
        messages = await conn.fetch(
            "SELECT id,sender_type,body,created_at FROM support_messages WHERE ticket_id=$1 ORDER BY created_at",
            ticket_id,
        )
    return {"ticket": _ticket_payload(ticket), "messages": [dict(row) | {"created_at": row["created_at"].isoformat()} for row in messages]}


@router.post("/api/support/tickets/{ticket_id}/messages")
async def reply_support_ticket(
    ticket_id: int,
    data: MessageCreateIn,
    authorization: Optional[str] = Header(None),
    x_support_key: Optional[str] = Header(None, alias="X-Support-Key"),
):
    body = _message(data.message)
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            actor = await _support_actor(conn, authorization, x_support_key)
            clause, value = _ownership(actor)
            ticket = await conn.fetchrow(f"SELECT t.* FROM support_tickets t WHERE t.id=$1 AND {clause}", ticket_id, value)
            if not ticket:
                raise HTTPException(status_code=404, detail="Murojaat topilmadi")
            if ticket["status"] == "closed":
                raise HTTPException(status_code=400, detail="Yopilgan murojaatga xabar yozib bo'lmaydi")
            await conn.execute(
                "INSERT INTO support_messages(ticket_id,sender_type,sender_user_id,body) VALUES($1,'user',$2,$3)",
                ticket_id, actor["user_id"], body,
            )
            await conn.execute(
                "UPDATE support_tickets SET status='waiting_admin',last_message_at=NOW(),updated_at=NOW() WHERE id=$1",
                ticket_id,
            )
            await notify_admin(
                conn,
                "Yordam murojaatiga yangi xabar",
                f"#{ticket_id}: {body[:140]}",
                kind="task",
                action_url=f"/admin?section=support&ticket={ticket_id}",
                metadata={"event": "support_reply", "ticket_id": ticket_id},
            )
    return {"ok": True}


@router.get("/api/admin/support/tickets")
async def admin_support_tickets(
    status: str = Query("all"),
    x_admin_secret: str = Header("", alias="X-Admin-Secret"),
):
    _check_admin(x_admin_secret)
    where, params = "", []
    if status != "all":
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail="Holat noto'g'ri")
        where, params = "WHERE t.status=$1", [status]
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.*,
                   (SELECT body FROM support_messages WHERE ticket_id=t.id ORDER BY created_at DESC LIMIT 1) AS last_message,
                   (SELECT COUNT(*) FROM support_messages WHERE ticket_id=t.id AND sender_type='user' AND read_at IS NULL) AS unread_count
            FROM support_tickets t {where}
            ORDER BY CASE WHEN t.status='waiting_admin' THEN 0 ELSE 1 END,t.last_message_at DESC LIMIT 100
            """,
            *params,
        )
    return [_ticket_payload(row) | {"contact_email": row["contact_email"], "user_id": row["user_id"]} for row in rows]


@router.get("/api/admin/support/tickets/{ticket_id}")
async def admin_support_ticket(ticket_id: int, x_admin_secret: str = Header("", alias="X-Admin-Secret")):
    _check_admin(x_admin_secret)
    db = await get_pool()
    async with db.acquire() as conn:
        ticket = await conn.fetchrow("SELECT * FROM support_tickets WHERE id=$1", ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Murojaat topilmadi")
        await conn.execute(
            "UPDATE support_messages SET read_at=COALESCE(read_at,NOW()) WHERE ticket_id=$1 AND sender_type='user'",
            ticket_id,
        )
        messages = await conn.fetch(
            "SELECT id,sender_type,body,created_at FROM support_messages WHERE ticket_id=$1 ORDER BY created_at",
            ticket_id,
        )
    return {
        "ticket": _ticket_payload(ticket) | {"contact_email": ticket["contact_email"], "user_id": ticket["user_id"]},
        "messages": [dict(row) | {"created_at": row["created_at"].isoformat()} for row in messages],
    }


@router.post("/api/admin/support/tickets/{ticket_id}/messages")
async def admin_reply_support_ticket(
    ticket_id: int,
    data: MessageCreateIn,
    x_admin_secret: str = Header("", alias="X-Admin-Secret"),
):
    _check_admin(x_admin_secret)
    body = _message(data.message)
    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            ticket = await conn.fetchrow("SELECT * FROM support_tickets WHERE id=$1", ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="Murojaat topilmadi")
            await conn.execute(
                "INSERT INTO support_messages(ticket_id,sender_type,body) VALUES($1,'admin',$2)",
                ticket_id, body,
            )
            await conn.execute(
                "UPDATE support_tickets SET status='waiting_user',last_message_at=NOW(),updated_at=NOW() WHERE id=$1",
                ticket_id,
            )
            if ticket["user_id"]:
                await create_notification(
                    conn,
                    recipient_user_id=ticket["user_id"],
                    kind="info",
                    title="Yordam markazidan javob keldi",
                    message=body[:240],
                    action_url=f"/profile?support={ticket_id}",
                    metadata={"event": "support_admin_reply", "ticket_id": ticket_id},
                )
    try:
        await send_email(
            ticket["contact_email"],
            ticket["contact_name"],
            f"IELTS Mock SS — #{ticket_id} murojaatingizga javob",
            f"<p>Assalomu alaykum, <b>{html.escape(ticket['contact_name'])}</b>!</p>"
            f"<p>Yordam markazidan yangi javob:</p><div style='padding:14px;border-radius:10px;background:#eef3ff'>"
            f"{html.escape(body).replace(chr(10), '<br>')}</div><p>Javob yozish uchun IELTS Mock SS saytiga kiring.</p>",
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/admin/support/tickets/{ticket_id}/status")
async def admin_update_support_status(
    ticket_id: int,
    data: TicketStatusIn,
    x_admin_secret: str = Header("", alias="X-Admin-Secret"),
):
    _check_admin(x_admin_secret)
    if data.status not in STATUSES:
        raise HTTPException(status_code=400, detail="Holat noto'g'ri")
    db = await get_pool()
    async with db.acquire() as conn:
        ticket = await conn.fetchrow(
            "UPDATE support_tickets SET status=$1,updated_at=NOW() WHERE id=$2 RETURNING user_id",
            data.status, ticket_id,
        )
        if not ticket:
            raise HTTPException(status_code=404, detail="Murojaat topilmadi")
        if ticket["user_id"] and data.status in {"resolved", "closed"}:
            await create_notification(
                conn,
                recipient_user_id=ticket["user_id"],
                kind="success",
                title="Yordam murojaati yakunlandi",
                message=f"#{ticket_id} murojaatingiz holati: {data.status}.",
                action_url=f"/profile?support={ticket_id}",
                metadata={"event": "support_status", "ticket_id": ticket_id, "status": data.status},
            )
    return {"ok": True, "status": data.status}
