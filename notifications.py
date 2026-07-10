"""
Head-teacher / teacher uchun bildirishnomalar.
Qoida: agar foydalanuvchi Telegram ulagan bo'lsa (telegram_chat_id bor) — Telegram orqali,
aks holda email orqali yuboriladi. Bu mavjud telegram.py/auth.send_email patternini
qayta ishlatadi, alohida email/SMS provayder qo'shilmagan.
"""

from auth import send_email
from telegram import send_telegram

SECTION_NAMES = {"listening": "Listening", "reading": "Reading", "writing": "Writing", "speaking": "Speaking"}


async def notify_person(user_row: dict, subject: str, email_html: str, telegram_text: str):
    chat_id = user_row.get("telegram_chat_id")
    if chat_id:
        sent = await send_telegram(chat_id, telegram_text)
        if sent:
            return
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
        "SELECT full_name, email, telegram_chat_id FROM users WHERE id = (SELECT owner_id FROM centers WHERE id=$1)",
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
    await notify_person(dict(owner), "Yangi teacher qo'shildi", html, text)


async def notify_teacher_new_result(conn, group_id: int, student_name: str, section: str, band):
    """Guruhdagi talaba yangi natija topshirganda o'sha guruh teacher'iga xabar."""
    if not group_id:
        return
    teacher = await conn.fetchrow(
        "SELECT full_name, email, telegram_chat_id FROM users WHERE id = (SELECT teacher_id FROM groups WHERE id=$1)",
        group_id
    )
    if not teacher:
        return
    section_name = SECTION_NAMES.get(section, section)
    band_text = f", Band {band}" if band else ""
    text = f"📊 <b>Yangi natija</b>\n\n👤 {student_name}\n📝 {section_name}{band_text}"
    html = f"<p><b>{student_name}</b> {section_name} bo'limini topshirdi{band_text}.</p>"
    await notify_person(dict(teacher), "Guruhingizda yangi natija", html, text)
