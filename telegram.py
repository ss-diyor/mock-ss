import os
import httpx

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
# Speaking audiolarini saqlash uchun maxsus Telegram guruhi (xususiy yoki public)
# Bu guruhga bot admin bo'lishi kerak
TELEGRAM_SPEAKING_CHAT_ID = os.environ.get("TELEGRAM_SPEAKING_CHAT_ID", TELEGRAM_ADMIN_CHAT_ID)


async def send_telegram(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Telegram Bot API orqali xabar yuborish. Muvaffaqiyatli bo'lsa True qaytaradi."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode
                }
            )
            return response.status_code == 200
    except Exception:
        return False


async def notify_admin_new_result(
    full_name: str, email: str, section: str,
    score=None, total=None, band=None
):
    """Yangi test natijasi haqida admin'ga xabar yuborish."""
    if not TELEGRAM_ADMIN_CHAT_ID:
        return

    section_names = {"listening": "Listening", "reading": "Reading", "writing": "Writing"}
    section_name = section_names.get(section, section)

    if section == "writing":
        details = "Tekshirish kutilmoqda"
    else:
        details = f"{score}/{total} to'g'ri, Band {band}" if score and total and band else "—"

    text = (
        f"🆕 <b>Yangi natija topshirildi</b>\n\n"
        f"👤 {full_name}\n"
        f"📧 {email}\n"
        f"📝 Bo'lim: {section_name}\n"
        f"📊 {details}"
    )
    await send_telegram(TELEGRAM_ADMIN_CHAT_ID, text)


async def notify_admin_new_user(full_name: str, email: str, username: str):
    """Yangi ro'yxatdan o'tish haqida admin'ga xabar yuborish."""
    if not TELEGRAM_ADMIN_CHAT_ID:
        return

    text = (
        f"👋 <b>Yangi foydalanuvchi ro'yxatdan o'tdi</b>\n\n"
        f"👤 {full_name}\n"
        f"🔖 @{username}\n"
        f"📧 {email}"
    )
    await send_telegram(TELEGRAM_ADMIN_CHAT_ID, text)


async def notify_user_result_ready(
    chat_id: str, full_name: str, section: str,
    band=None, score=None, total=None
):
    """Natija tayyor bo'lganda foydalanuvchiga Telegram xabar yuborish."""
    if not chat_id:
        return

    section_names = {"listening": "Listening", "reading": "Reading", "writing": "Writing"}
    section_name = section_names.get(section, section)

    if section == "writing":
        details = f"Band: {band}" if band else "Tekshirilmoqda"
    else:
        details = f"{score}/{total} to'g'ri, Band {band}" if score and total and band else "—"

    text = (
        f"✅ <b>{section_name} natijangiz tayyor!</b>\n\n"
        f"👤 {full_name}\n"
        f"📊 {details}\n\n"
        f"🔗 Batafsil: https://ielts.sultanov.space/profile"
    )
    await send_telegram(chat_id, text)


async def send_voice_to_telegram(
    audio_bytes: bytes,
    filename: str,
    caption: str,
    chat_id: str | None = None
) -> str | None:
    """Speaking audio faylini Telegram guruhiga yuborish. Muvaffaqiyatli bo'lsa file_id qaytaradi."""
    target = chat_id or TELEGRAM_SPEAKING_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio",
                data={
                    "chat_id": target,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "audio": (filename, audio_bytes, "audio/ogg")
                }
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("result", {}).get("audio", {}).get("file_id")
            return None
    except Exception:
        return None


async def notify_admin_new_speaking(
    full_name: str, email: str, file_id: str | None = None
):
    """Yangi speaking topshirilganda admin'ga xabar."""
    if not TELEGRAM_ADMIN_CHAT_ID:
        return

    text = (
        f"🎤 <b>Yangi Speaking topshirildi</b>\n\n"
        f"👤 {full_name}\n"
        f"📧 {email}\n"
        f"📊 Tekshirish kutilmoqda"
    )
    await send_telegram(TELEGRAM_ADMIN_CHAT_ID, text)


async def notify_user_speaking_graded(
    chat_id: str, full_name: str, band: float, feedback: str = None
):
    """Speaking baholanganda foydalanuvchiga Telegram xabar."""
    if not chat_id:
        return

    text = (
        f"🎤 <b>Speaking natijangiz baholandi!</b>\n\n"
        f"👤 {full_name}\n"
        f"🏆 Band: {band}\n"
    )
    if feedback:
        text += f"💬 Izoh: {feedback}\n"

    text += f"\n🔗 Batafsil: https://ielts.sultanov.space/profile"
    await send_telegram(chat_id, text)


async def send_verification_code_telegram(chat_id: str, code: str):
    """Email tasdiqlash kodini Telegram orqali ham yuborish."""
    if not chat_id:
        return

    text = (
        f"🔐 <b>IELTS Mock SS— Tasdiqlash kodi</b>\n\n"
        f"Sizning kodingiz: <code>{code}</code>\n\n"
        f"⏱ Kod 15 daqiqa davomida amal qiladi."
    )
    await send_telegram(chat_id, text)


async def notify_user_writing_graded(
    chat_id: str, full_name: str, band: float, feedback: str = None
):
    """Writing baholanganda foydalanuvchiga Telegram xabar."""
    if not chat_id:
        return

    text = (
        f"📝 <b>Writing natijangiz baholandi!</b>\n\n"
        f"👤 {full_name}\n"
        f"🏆 Band: {band}\n"
    )
    if feedback:
        text += f"💬 Izoh: {feedback}\n"

    text += f"\n🔗 Batafsil: https://ielts.sultanov.space/profile"
    await send_telegram(chat_id, text)