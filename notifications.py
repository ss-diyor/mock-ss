"""Teacher va tashkilot rahbari uchun email hamda Telegram bildirishnomalari."""

from auth import send_email
from notification_center import create_notification
from telegram import send_telegram

SECTION_NAMES = {"listening": "Listening", "reading": "Reading", "writing": "Writing", "speaking": "Speaking"}


async def notify_person(user_row: dict, subject: str, email_html: str, telegram_text: str):
    chat_id = user_row.get("telegram_chat_id")
    if chat_id:
        await send_telegram(chat_id, telegram_text)
    email = user_row.get("email")
    if not email:
        return
    try:
        await send_email(email, user_row.get("full_name", ""), subject, email_html)
    except Exception:
        pass


async def notify_head_teacher_new_teacher(conn, center_id: int, teacher_name: str, teacher_email: str, group_name: str):
    """Teacher-invite orqali yangi teacher ro'yxatdan o'tganda markaz rahbariga xabar."""
    owner = await conn.fetchrow(
        "SELECT id, full_name, email, telegram_chat_id FROM users WHERE id = (SELECT owner_id FROM centers WHERE id=$1)",
        center_id
    )
    if not owner:
        return
    text = (
        f"👨\u200d🏫 <b>Yangi teacher qo'shildi</b>\n\n"
        f"Guruh: {group_name}\n"
        f"Teacher: {teacher_name} ({teacher_email})"
    )
    html = f"<p>Yangi teacher <b>{teacher_name}</b> ({teacher_email}) \u2014 \"{group_name}\" guruhiga biriktirildi.</p>"
    await create_notification(
        conn,
        recipient_user_id=owner["id"],
        kind="success",
        title="Yangi teacher qo'shildi",
        message=f"{teacher_name} {group_name} guruhiga teacher sifatida qo'shildi.",
        action_url="/head-teacher",
        metadata={"event": "teacher_joined"},
    )
    await notify_person(dict(owner), "Yangi teacher qo'shildi", html, text)


async def notify_teacher_new_result(conn, group_id: int, student_name: str, section: str, band):
    """Guruhdagi talaba yangi natija topshirganda o'sha guruh teacher'iga xabar."""
    if not group_id:
        return
    teacher = await conn.fetchrow(
        "SELECT id, full_name, email, telegram_chat_id FROM users WHERE id = (SELECT teacher_id FROM groups WHERE id=$1)",
        group_id
    )
    if not teacher:
        return
    section_name = SECTION_NAMES.get(section, section)
    band_text = f", Band {band}" if band else ""
    text = f"📊 <b>Yangi natija</b>\n\n👤 {student_name}\n📝 {section_name}{band_text}"
    html = f"<p><b>{student_name}</b> {section_name} bo'limini topshirdi{band_text}.</p>"
    await create_notification(
        conn,
        recipient_user_id=teacher["id"],
        kind="task" if section in {"writing", "speaking"} else "info",
        title="Guruhingizda yangi natija",
        message=f"{student_name} {section_name} bo'limini topshirdi{band_text}.",
        action_url="/teacher",
        metadata={"event": "student_result", "section": section},
    )
    await notify_person(dict(teacher), "Guruhingizda yangi natija", html, text)


async def notify_head_teacher_new_result(conn, center_id: int, student_name: str, section: str, band):
    """Tashkilot rahbari/direktoriga yangi natija haqida xabar."""
    owner = await conn.fetchrow(
        "SELECT id, full_name, email, telegram_chat_id FROM users WHERE id=(SELECT owner_id FROM centers WHERE id=$1)",
        center_id
    )
    if not owner:
        return
    section_name = SECTION_NAMES.get(section, section)
    band_text = f", Band {band}" if band else ""
    text = f"📊 <b>Tashkilotda yangi natija</b>\n\n👤 {student_name}\n📝 {section_name}{band_text}"
    html = f"<p><b>{student_name}</b> {section_name} bo'limini topshirdi{band_text}.</p>"
    await create_notification(
        conn,
        recipient_user_id=owner["id"],
        kind="info",
        title="Tashkilotda yangi natija",
        message=f"{student_name} {section_name} bo'limini topshirdi{band_text}.",
        action_url="/head-teacher",
        metadata={"event": "organization_result", "section": section},
    )
    staff_rows = await conn.fetch(
        """
        SELECT DISTINCT ss.user_id
        FROM school_staff ss
        JOIN school_positions position ON position.id=ss.position_id
        WHERE ss.center_id=$1 AND ss.is_active=TRUE AND ss.deleted_at IS NULL
          AND position.deleted_at IS NULL AND position.permissions ? 'view_results'
          AND ss.user_id<>$2
        """,
        center_id,
        owner["id"],
    )
    for staff in staff_rows:
        await create_notification(
            conn,
            recipient_user_id=staff["user_id"],
            kind="info",
            title="Maktabda yangi natija",
            message=f"{student_name} {section_name} bo'limini topshirdi{band_text}.",
            action_url="/school-staff",
            metadata={"event": "school_result", "section": section},
        )
    await notify_person(dict(owner), "Tashkilotingizda yangi natija", html, text)
