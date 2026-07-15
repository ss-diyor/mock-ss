"""In-app notification storage, authenticated user API, and shared helpers."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from db import get_pool


router = APIRouter()
ALLOWED_KINDS = {"info", "success", "warning", "task"}


async def ensure_notification_tables(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id BIGSERIAL PRIMARY KEY,
            recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            recipient_role TEXT,
            kind TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            action_url TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            read_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CHECK (recipient_user_id IS NOT NULL OR recipient_role IS NOT NULL)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS notifications_user_idx "
        "ON notifications(recipient_user_id, created_at DESC) WHERE recipient_user_id IS NOT NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS notifications_role_idx "
        "ON notifications(recipient_role, created_at DESC) WHERE recipient_role IS NOT NULL"
    )


def _clean_text(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_action_url(value: Optional[str]) -> Optional[str]:
    value = str(value or "").strip()
    return value[:500] if value.startswith("/") and not value.startswith("//") else None


async def create_notification(
    conn,
    *,
    title: str,
    message: str,
    recipient_user_id: Optional[int] = None,
    recipient_role: Optional[str] = None,
    kind: str = "info",
    action_url: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[int]:
    if recipient_user_id is None and not recipient_role:
        return None
    title = _clean_text(title, 140)
    message = _clean_text(message, 1000)
    if not title or not message:
        return None
    kind = kind if kind in ALLOWED_KINDS else "info"
    return await conn.fetchval(
        """
        INSERT INTO notifications(
            recipient_user_id, recipient_role, kind, title, message, action_url, metadata
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
        RETURNING id
        """,
        recipient_user_id,
        recipient_role,
        kind,
        title,
        message,
        _clean_action_url(action_url),
        json.dumps(metadata or {}),
    )


async def notify_admin(conn, title: str, message: str, *, kind: str = "info", action_url: str = "/admin", metadata: Optional[dict] = None):
    return await create_notification(
        conn,
        recipient_role="admin",
        title=title,
        message=message,
        kind=kind,
        action_url=action_url,
        metadata=metadata,
    )


def _row_payload(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "message": row["message"],
        "action_url": row["action_url"],
        "metadata": row["metadata"] or {},
        "is_read": row["read_at"] is not None,
        "read_at": row["read_at"].isoformat() if row["read_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def fetch_notifications(conn, *, user_id: Optional[int] = None, role: Optional[str] = None, limit: int = 30) -> dict:
    if user_id is not None:
        clause, value = "recipient_user_id=$1", user_id
    else:
        clause, value = "recipient_role=$1", role
    rows = await conn.fetch(
        f"""
        SELECT id, kind, title, message, action_url, metadata, read_at, created_at
        FROM notifications
        WHERE {clause}
        ORDER BY created_at DESC
        LIMIT $2
        """,
        value,
        limit,
    )
    unread_count = await conn.fetchval(
        f"SELECT COUNT(*) FROM notifications WHERE {clause} AND read_at IS NULL",
        value,
    )
    return {"unread_count": unread_count, "items": [_row_payload(row) for row in rows]}


async def mark_notification_read(conn, notification_id: int, *, user_id: Optional[int] = None, role: Optional[str] = None) -> bool:
    if user_id is not None:
        clause, value = "recipient_user_id=$2", user_id
    else:
        clause, value = "recipient_role=$2", role
    result = await conn.execute(
        f"UPDATE notifications SET read_at=COALESCE(read_at,NOW()) WHERE id=$1 AND {clause}",
        notification_id,
        value,
    )
    return result != "UPDATE 0"


async def mark_all_notifications_read(conn, *, user_id: Optional[int] = None, role: Optional[str] = None) -> int:
    if user_id is not None:
        clause, value = "recipient_user_id=$1", user_id
    else:
        clause, value = "recipient_role=$1", role
    result = await conn.execute(
        f"UPDATE notifications SET read_at=NOW() WHERE {clause} AND read_at IS NULL",
        value,
    )
    return int(result.split()[-1])


@router.get("/api/notifications")
async def my_notifications(
    limit: int = Query(30, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    db = await get_pool()
    async with db.acquire() as conn:
        return await fetch_notifications(conn, user_id=current_user["id"], limit=limit)


@router.post("/api/notifications/{notification_id}/read")
async def read_my_notification(notification_id: int, current_user: dict = Depends(get_current_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        updated = await mark_notification_read(conn, notification_id, user_id=current_user["id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Bildirishnoma topilmadi")
    return {"ok": True}


@router.post("/api/notifications/read-all")
async def read_all_my_notifications(current_user: dict = Depends(get_current_user)):
    db = await get_pool()
    async with db.acquire() as conn:
        updated = await mark_all_notifications_read(conn, user_id=current_user["id"])
    return {"ok": True, "updated": updated}
