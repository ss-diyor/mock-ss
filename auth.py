import os
import re
import io
import secrets
import random
import bcrypt
import jwt
import httpx
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Depends
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from typing import Optional
from PIL import Image

from db import get_pool
from scoring import get_band_score

router = APIRouter(prefix="/api/auth", tags=["auth"])

def require_secret_env(name: str, insecure_default: str, min_length: int = 32) -> str:
    value = os.environ.get(name)
    if not value or value == insecure_default or len(value) < min_length:
        raise RuntimeError(
            f"{name} must be set to a strong secret with at least {min_length} characters"
        )
    return value


JWT_SECRET = require_secret_env("JWT_SECRET", "change-me-in-production")
JWT_ALGO = "HS256"
JWT_EXPIRE_DAYS = 30

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
MAX_AVATAR_BYTES = 5 * 1024 * 1024

# Email konfiguratsiyasi (Resend) — main.py bilan bir xil environment variable'lar
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@ielts.sultanov.space")

VERIFICATION_CODE_TTL_MINUTES = 15
VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
RESET_TOKEN_TTL_MINUTES = 30
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "https://ielts.sultanov.space")


async def ensure_users_table():
    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                bio TEXT,
                avatar_data BYTEA,
                avatar_mime TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Email tasdiqlash uchun ustunlar
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_code TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_expires TIMESTAMP")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_sent_at TIMESTAMP")
        # Admin tomonidan bloklash uchun ustun
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")

        # Telegram va Referral
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by INTEGER REFERENCES users(id)")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0")

        # Rate limiting
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id SERIAL PRIMARY KEY,
                login_key TEXT NOT NULL,
                attempted_at TIMESTAMP DEFAULT NOW(),
                success BOOLEAN DEFAULT FALSE
            )
        """)

        # Parolni tiklash tokenlari
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)


# ─── Email yuborish (Resend API) ────────────────────────────────────────────────

async def send_email(to_email: str, to_name: str, subject: str, html_body: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY sozlanmagan")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": f"IELTS Mock SS <{EMAIL_FROM}>",
                "to": [to_email],
                "subject": subject,
                "html": html_body
            }
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Resend xatosi: {response.text}")


def build_verification_email(name: str, code: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif; max-width:520px; margin:0 auto; padding:24px; border:1px solid #c9d8ff; border-radius:12px;">
      <h2 style="color:#1a56e8;">Email manzilingizni tasdiqlang</h2>
      <p>Assalomu alaykum, {name}!</p>
      <p>IELTS Mock SS akkauntingizni faollashtirish uchun quyidagi kodni kiriting:</p>
      <div style="margin:20px 0; text-align:center;">
        <span style="display:inline-block; font-family:'Courier New',monospace; font-size:32px; font-weight:700; letter-spacing:8px; color:#1a56e8; background:#eef3ff; padding:14px 24px; border-radius:10px;">{code}</span>
      </div>
      <p style="color:#4a5978; font-size:13px;">Kod {VERIFICATION_CODE_TTL_MINUTES} daqiqa davomida amal qiladi. Agar siz bu so'rovni yubormagan bo'lsangiz, xatni e'tiborsiz qoldiring.</p>
      <p style="margin-top:20px; color:#4a5978; font-size:13px;">
        <a href="{FRONTEND_BASE_URL}" style="color:#1a56e8;">ielts.sultanov.space</a>
      </p>
      <p style="color:#4a5978; font-size:12px; margin-top:16px;"> © 2026-2027 Bo'stonliq tuman ixtisoslashtirilgan maktabi</p>
    </div>
    """


def build_reset_email(name: str, reset_link: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif; max-width:520px; margin:0 auto; padding:24px; border:1px solid #c9d8ff; border-radius:12px;">
      <h2 style="color:#1a56e8;">Parolni tiklash</h2>
      <p>Assalomu alaykum, {name}!</p>
      <p>Parolingizni tiklash uchun so'rov yubordingiz. Yangi parol o'rnatish uchun quyidagi tugmani bosing:</p>
      <div style="margin:24px 0; text-align:center;">
        <a href="{reset_link}" style="display:inline-block; background:#1a56e8; color:#fff; text-decoration:none; font-weight:600; padding:12px 28px; border-radius:8px;">Yangi parol o'rnatish</a>
      </div>
      <p style="color:#4a5978; font-size:13px;">Havola {RESET_TOKEN_TTL_MINUTES} daqiqa davomida amal qiladi. Agar siz bu so'rovni yubormagan bo'lsangiz, xatni e'tiborsiz qoldiring — parolingiz o'zgarmaydi.</p>
      <p style="color:#4a5978; font-size:12px; margin-top:16px;"> © 2026-2027 Bo'stonliq tuman ixtisoslashtirilgan maktabi</p>
    </div>
    """


def generate_verification_code() -> str:
    return f"{random.randint(0, 999999):06d}"


# ─── Models ─────────────────────────────────────────────────────────────────────

class TelegramData(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str

class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    full_name: str
    password: str
    referral_code: Optional[str] = None
    telegram_data: Optional[TelegramData] = None
    group_invite_code: Optional[str] = None    # student sifatida guruhga qo'shilish
    teacher_invite_code: Optional[str] = None  # teacher sifatida guruhga rahbar bo'lish

class TelegramLoginIn(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


class LoginIn(BaseModel):
    login: str  # email yoki username
    password: str


class ProfileUpdateIn(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    telegram_chat_id: Optional[str] = None


class VerifyEmailIn(BaseModel):
    email: EmailStr
    code: str


class ResendVerificationIn(BaseModel):
    email: EmailStr


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


# ─── Password / Token yordamchilari ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def hash_secret_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def verify_telegram_data(data: dict) -> bool:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return False
    if time.time() - data.get("auth_date", 0) > 3600:
        return False
        
    received_hash = data.get("hash")
    data_check_arr = []
    for key, value in data.items():
        if key != "hash" and value is not None:
            data_check_arr.append(f"{key}={value}")
    data_check_arr.sort()
    data_check_string = "\n".join(data_check_arr)
    
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    return hmac.compare_digest(expected_hash, received_hash)


async def check_rate_limit(login_key: str):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as recent_failures 
            FROM login_attempts 
            WHERE login_key = $1 
              AND success = FALSE 
              AND attempted_at > NOW() - INTERVAL '15 minutes'
            """,
            login_key
        )
        if row and row["recent_failures"] >= 5:
            last_attempt = await conn.fetchrow(
                """
                SELECT attempted_at 
                FROM login_attempts 
                WHERE login_key = $1 AND success = FALSE 
                ORDER BY attempted_at DESC LIMIT 1
                """,
                login_key
            )
            if last_attempt:
                elapsed = (datetime.utcnow() - last_attempt["attempted_at"]).total_seconds()
                remaining = max(0, int(15 * 60 - elapsed))
                if remaining > 0:
                    minutes = remaining // 60
                    seconds = remaining % 60
                    raise HTTPException(
                        status_code=429, 
                        detail=f"Juda ko'p urinish. {minutes} daqiqa {seconds} soniyadan so'ng qayta urinib ko'ring."
                    )

async def record_login_attempt(login_key: str, success: bool):
    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO login_attempts (login_key, success) VALUES ($1, $2)",
            login_key, success
        )
        if success:
            await conn.execute("DELETE FROM login_attempts WHERE login_key = $1", login_key)


async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Kirish talab qilinadi")

    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessiya muddati tugagan, qayta kiring")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Yaroqsiz token")

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Yaroqsiz token")

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, email, full_name, bio, email_verified, is_suspended,
                   telegram_chat_id, referral_code, referral_count,
                   role, group_id, center_id,
                   (avatar_mime IS NOT NULL) AS has_avatar
            FROM users WHERE id=$1
            """,
            user_id
        )
    if not row:
        raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi")
    if row["is_suspended"]:
        raise HTTPException(status_code=403, detail="Hisobingiz vaqtincha bloklangan")
    return dict(row)


async def get_current_teacher(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Faqat o'qituvchilar uchun")
    return current_user


async def get_current_head_teacher(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "head_teacher":
        raise HTTPException(status_code=403, detail="Faqat markaz rahbarlari uchun")
    if not current_user.get("center_id"):
        raise HTTPException(status_code=403, detail="Sizga hech qanday markaz biriktirilmagan")
    return current_user


def public_profile(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "full_name": row["full_name"],
        "bio": row["bio"],
        "email_verified": bool(row.get("email_verified", False)),
        "avatar_url": f"/api/auth/avatar/{row['username']}" if row.get("has_avatar") else None,
        "telegram_chat_id": row.get("telegram_chat_id"),
        "referral_code": row.get("referral_code"),
        "referral_count": row.get("referral_count", 0),
        "role": row.get("role", "student")
    }


# ─── Auth endpointlari ───────────────────────────────────────────────────────────

@router.post("/register")
async def register(data: RegisterIn):
    username = data.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username 3-20 belgidan, faqat kichik lotin harflari, raqam va _ bo'lishi kerak"
        )
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Parol kamida 6 belgidan iborat bo'lishi kerak")
    if not data.full_name.strip():
        raise HTTPException(status_code=400, detail="To'liq ismni kiriting")

    if data.group_invite_code and data.teacher_invite_code:
        raise HTTPException(status_code=400, detail="Faqat bitta taklif kodini kiriting")

    code = generate_verification_code()
    now = datetime.utcnow()
    expires = now + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)

    role_val = "student"
    group_id_val = None
    center_id_val = None
    teacher_group_id = None   # teacher_invite_code orqali kelsa, keyin groups.teacher_id shu yerga yoziladi
    roster_row_id = None      # group_invite_code roster orqali kelsa, used=TRUE qilish uchun

    db = await get_pool()
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id FROM users WHERE username=$1 OR email=$2",
                username, data.email.lower()
            )
            if existing:
                raise HTTPException(status_code=409, detail="Bu username yoki email allaqachon ro'yxatdan o'tgan")

            # ── Teacher-invite: guruhga rahbar sifatida qo'shilish ──
            if data.teacher_invite_code:
                group_row = await conn.fetchrow(
                    "SELECT id, name, center_id, is_active, teacher_id, teacher_invite_expires_at FROM groups WHERE teacher_invite_code = $1",
                    data.teacher_invite_code
                )
                if not group_row:
                    raise HTTPException(status_code=400, detail="Taklif kodi noto'g'ri yoki allaqachon ishlatilgan")
                if group_row["teacher_id"] is not None:
                    raise HTTPException(status_code=400, detail="Bu guruhda allaqachon teacher bor")
                if group_row["teacher_invite_expires_at"] and group_row["teacher_invite_expires_at"].replace(tzinfo=None) < datetime.utcnow():
                    raise HTTPException(status_code=400, detail="Taklif kodi muddati tugagan")
                if not group_row["is_active"]:
                    raise HTTPException(status_code=400, detail="Bu guruh faol emas")
                
                await conn.execute("UPDATE groups SET teacher_invite_code = NULL WHERE id = $1", group_row["id"])
                
                role_val = "teacher"
                center_id_val = group_row["center_id"]
                teacher_group_id = group_row["id"]
                group_id_val = group_row["id"]

            # ── Group-invite: talaba sifatida guruhga qo'shilish ──
            elif data.group_invite_code:
                group_row = await conn.fetchrow(
                    "SELECT id, center_id, is_active FROM groups WHERE invite_code=$1",
                    data.group_invite_code
                )
                if not group_row:
                    raise HTTPException(status_code=404, detail="Guruh topilmadi")
                if not group_row["is_active"]:
                    raise HTTPException(status_code=400, detail="Bu guruh faol emas")

                roster_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM group_roster_emails WHERE group_id=$1", group_row["id"]
                )
                if roster_count > 0:
                    roster_row = await conn.fetchrow(
                        "SELECT id FROM group_roster_emails WHERE group_id=$1 AND email=$2 AND used=FALSE",
                        group_row["id"], data.email.lower()
                    )
                    if not roster_row:
                        raise HTTPException(
                            status_code=403,
                            detail="Sizning emailingiz bu guruh ro'yxatida topilmadi. Markaz rahbariga murojaat qiling"
                        )
                    roster_row_id = roster_row["id"]

                from groups_db import get_center_limits
                _, max_students = await get_center_limits(conn, group_row["center_id"])
                student_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM users u
                    JOIN groups g ON u.group_id = g.id
                    WHERE g.center_id = $1
                    """,
                    group_row["center_id"]
                )
                if student_count >= max_students:
                    raise HTTPException(status_code=400, detail="Markazdagi talabalar soni limitiga yetdi")

                group_id_val = group_row["id"]
                center_id_val = group_row["center_id"]

            referral_code_val = secrets.token_urlsafe(8)
            referred_by_id = None

            if data.referral_code:
                ref_user = await conn.fetchrow("SELECT id FROM users WHERE referral_code=$1", data.referral_code)
                if ref_user:
                    referred_by_id = ref_user["id"]
                    await conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE id=$1", referred_by_id)

            telegram_chat_id = None
            if data.telegram_data:
                if not verify_telegram_data(data.telegram_data.dict()):
                    raise HTTPException(status_code=400, detail="Telegram ma'lumotlari yaroqsiz")
                telegram_chat_id = str(data.telegram_data.id)

            row = await conn.fetchrow(
                """
                INSERT INTO users (username, email, full_name, password_hash,
                                    verification_code, verification_expires, verification_sent_at,
                                    referral_code, referred_by, telegram_chat_id,
                                    role, group_id, center_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                RETURNING id, username, email, full_name, bio, email_verified, telegram_chat_id,
                          referral_code, referral_count, role, group_id, center_id
                """,
                username, data.email.lower(), data.full_name.strip(), hash_password(data.password),
                hash_secret_value(code), expires, now, referral_code_val, referred_by_id, telegram_chat_id,
                role_val, group_id_val, center_id_val
            )

            if teacher_group_id:
                await conn.execute("UPDATE groups SET teacher_id=$1 WHERE id=$2", row["id"], teacher_group_id)

            if roster_row_id:
                await conn.execute(
                    "UPDATE group_roster_emails SET used=TRUE, used_by=$1 WHERE id=$2",
                    row["id"], roster_row_id
                )

    try:
        await send_email(
            row["email"], row["full_name"],
            "IELTS Mock SS — Email tasdiqlash kodi",
            build_verification_email(row["full_name"], code)
        )
    except Exception:
        pass

    try:
        from telegram import notify_admin_new_user
        await notify_admin_new_user(row["full_name"], row["email"], row["username"])
    except Exception:
        pass

    if role_val == "teacher" and teacher_group_id:
        try:
            from notifications import notify_head_teacher_new_teacher
            db2 = await get_pool()
            async with db2.acquire() as conn2:
                group_info = await conn2.fetchrow("SELECT name FROM groups WHERE id=$1", teacher_group_id)
                await notify_head_teacher_new_teacher(
                    conn2, center_id_val, row["full_name"], row["email"],
                    group_info["name"] if group_info else ""
                )
        except Exception:
            pass

    profile = dict(row)
    profile["has_avatar"] = False
    token = create_token(row["id"])
    return {"token": token, "user": public_profile(profile)}


@router.post("/telegram-login")
async def telegram_login(data: TelegramLoginIn):
    if not verify_telegram_data(data.dict()):
        raise HTTPException(status_code=400, detail="Telegram ma'lumotlari yaroqsiz")
        
    telegram_id_str = str(data.id)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, email, full_name, bio, password_hash, email_verified, is_suspended,
                   telegram_chat_id, referral_code, referral_count, role, group_id, center_id,
                   (avatar_mime IS NOT NULL) AS has_avatar
            FROM users WHERE telegram_chat_id=$1
            """,
            telegram_id_str
        )
        
    if row:
        if row["is_suspended"]:
            raise HTTPException(status_code=403, detail="Hisobingiz vaqtincha bloklangan")
        token = create_token(row["id"])
        return {"requires_registration": False, "token": token, "user": public_profile(dict(row))}
    else:
        return {"requires_registration": True, "telegram_data": data.dict()}

@router.post("/login")
async def login(data: LoginIn):
    login_val = data.login.strip().lower()
    await check_rate_limit(login_val)

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, email, full_name, bio, password_hash, email_verified, is_suspended,
                   telegram_chat_id, referral_code, referral_count, role, group_id, center_id,
                   (avatar_mime IS NOT NULL) AS has_avatar
            FROM users WHERE username=$1 OR email=$1
            """,
            login_val
        )

    if not row or not verify_password(data.password, row["password_hash"]):
        await record_login_attempt(login_val, False)
        raise HTTPException(status_code=401, detail="Login yoki parol xato")
        
    await record_login_attempt(login_val, True)

    if row["is_suspended"]:
        raise HTTPException(status_code=403, detail="Hisobingiz vaqtincha bloklangan")

    token = create_token(row["id"])
    return {"token": token, "user": public_profile(dict(row))}


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"user": public_profile(current_user)}


VERIFY_EMAIL_MAX_ATTEMPTS = 5
VERIFY_EMAIL_WINDOW_MINUTES = 15


def verify_rate_limit_key(email: str) -> str:
    return f"verify-email:{email}"


async def check_verify_rate_limit(email: str):
    login_key = verify_rate_limit_key(email)
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) as recent_failures
            FROM login_attempts
            WHERE login_key = $1
              AND success = FALSE
              AND attempted_at > NOW() - INTERVAL '15 minutes'
            """,
            login_key
        )
        if row and row["recent_failures"] >= VERIFY_EMAIL_MAX_ATTEMPTS:
            last_attempt = await conn.fetchrow(
                """
                SELECT attempted_at
                FROM login_attempts
                WHERE login_key = $1 AND success = FALSE
                ORDER BY attempted_at DESC LIMIT 1
                """,
                login_key
            )
            if last_attempt:
                elapsed = (datetime.utcnow() - last_attempt["attempted_at"]).total_seconds()
                remaining = max(0, int(VERIFY_EMAIL_WINDOW_MINUTES * 60 - elapsed))
                if remaining > 0:
                    minutes = remaining // 60
                    seconds = remaining % 60
                    raise HTTPException(
                        status_code=429,
                        detail=f"Juda ko'p noto'g'ri urinish. {minutes} daqiqa {seconds} soniyadan so'ng qayta urinib ko'ring."
                    )


@router.post("/verify-email")
async def verify_email(data: VerifyEmailIn, current_user: dict = Depends(get_current_user)):
    email = data.email.strip().lower()
    code = data.code.strip()
    if email != current_user["email"].strip().lower():
        raise HTTPException(status_code=403, detail="Faqat o'zingizning emailingizni tasdiqlashingiz mumkin")

    await check_verify_rate_limit(email)

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email_verified, verification_code, verification_expires FROM users WHERE email=$1",
            email
        )
        if not row:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
        if row["email_verified"]:
            return {"message": "Email allaqachon tasdiqlangan", "email_verified": True}
        if not row["verification_code"] or not hmac.compare_digest(row["verification_code"], hash_secret_value(code)):
            await record_login_attempt(verify_rate_limit_key(email), False)
            raise HTTPException(status_code=400, detail="Tasdiqlash kodi noto'g'ri")
        if not row["verification_expires"] or row["verification_expires"] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Kod muddati tugagan, qaytadan so'rang")

        await conn.execute(
            """
            UPDATE users SET email_verified=TRUE, verification_code=NULL, verification_expires=NULL
            WHERE id=$1
            """,
            row["id"]
        )
        await record_login_attempt(verify_rate_limit_key(email), True)

    return {"message": "Email muvaffaqiyatli tasdiqlandi", "email_verified": True}


@router.post("/resend-verification")
async def resend_verification(data: ResendVerificationIn, current_user: dict = Depends(get_current_user)):
    email = data.email.strip().lower()
    if email != current_user["email"].strip().lower():
        raise HTTPException(status_code=403, detail="Faqat o'zingizning emailingizga kod yuborishingiz mumkin")

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, full_name, email_verified, verification_sent_at, telegram_chat_id FROM users WHERE email=$1",
            email
        )
        if not row:
            raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
        if row["email_verified"]:
            return {"message": "Email allaqachon tasdiqlangan"}

        if row["verification_sent_at"]:
            elapsed = (datetime.utcnow() - row["verification_sent_at"]).total_seconds()
            if elapsed < VERIFICATION_RESEND_COOLDOWN_SECONDS:
                wait = int(VERIFICATION_RESEND_COOLDOWN_SECONDS - elapsed)
                raise HTTPException(status_code=429, detail=f"Iltimos, {wait} soniyadan so'ng qayta urinib ko'ring")

        code = generate_verification_code()
        now = datetime.utcnow()
        expires = now + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
        await conn.execute(
            "UPDATE users SET verification_code=$1, verification_expires=$2, verification_sent_at=$3 WHERE id=$4",
            hash_secret_value(code), expires, now, row["id"]
        )

    try:
        await send_email(
            email, row["full_name"],
            "IELTS Mock SS — Email tasdiqlash kodi",
            build_verification_email(row["full_name"], code)
        )
        if row.get("telegram_chat_id"):
            from telegram import send_verification_code_telegram
            await send_verification_code_telegram(row["telegram_chat_id"], code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email yuborilmadi: {str(e)}")

    return {"message": "Tasdiqlash kodi qayta yuborildi"}


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordIn):
    email = data.email.strip().lower()
    generic_message = {"message": "Agar bu email ro'yxatdan o'tgan bo'lsa, xat yuborildi"}

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT id, full_name FROM users WHERE email=$1", email)
        if not row:
            return generic_message

        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
        await conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES ($1, $2, $3)",
            row["id"], hash_secret_value(token), expires
        )

    reset_link = f"{FRONTEND_BASE_URL}/profile?reset_token={token}"
    try:
        await send_email(
            email, row["full_name"],
            "IELTS Mock SS — Parolni tiklash",
            build_reset_email(row["full_name"], reset_link)
        )
    except Exception:
        pass  # Xavfsizlik uchun har doim bir xil javob qaytariladi

    return generic_message


@router.post("/reset-password")
async def reset_password(data: ResetPasswordIn):
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Parol kamida 6 belgidan iborat bo'lishi kerak")

    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token=$1",
            hash_secret_value(data.token)
        )
        if not row:
            raise HTTPException(status_code=400, detail="Havola yaroqsiz")
        if row["used"]:
            raise HTTPException(status_code=400, detail="Bu havoladan allaqachon foydalanilgan")
        if row["expires_at"] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Havola muddati tugagan, qaytadan so'rang")

        await conn.execute(
            "UPDATE users SET password_hash=$1 WHERE id=$2",
            hash_password(data.new_password), row["user_id"]
        )
        await conn.execute(
            "UPDATE password_reset_tokens SET used=TRUE WHERE id=$1",
            row["id"]
        )

    return {"message": "Parol muvaffaqiyatli yangilandi"}


@router.put("/profile")
async def update_profile(data: ProfileUpdateIn, current_user: dict = Depends(get_current_user)):
    fields, values = [], []
    idx = 1
    new_username = None

    if data.username is not None:
        new_username = data.username.strip().lower()
        if not USERNAME_RE.match(new_username):
            raise HTTPException(
                status_code=400,
                detail="Username 3-20 belgidan, faqat kichik lotin harflari, raqam va _ bo'lishi kerak"
            )
        fields.append(f"username=${idx}"); values.append(new_username); idx += 1

    if data.full_name is not None:
        if not data.full_name.strip():
            raise HTTPException(status_code=400, detail="To'liq ism bo'sh bo'lmasin")
        fields.append(f"full_name=${idx}"); values.append(data.full_name.strip()); idx += 1

    if data.bio is not None:
        fields.append(f"bio=${idx}"); values.append(data.bio.strip()[:280]); idx += 1

    if data.telegram_chat_id is not None:
        fields.append(f"telegram_chat_id=${idx}"); values.append(data.telegram_chat_id.strip() if data.telegram_chat_id.strip() else None); idx += 1

    if not fields:
        raise HTTPException(status_code=400, detail="Yangilanadigan maydon yo'q")

    db = await get_pool()
    async with db.acquire() as conn:
        if new_username is not None:
            dup = await conn.fetchrow(
                "SELECT id FROM users WHERE username=$1 AND id<>$2",
                new_username, current_user["id"]
            )
            if dup:
                raise HTTPException(status_code=409, detail="Bu username allaqachon band")

        values.append(current_user["id"])
        query = (
            f"UPDATE users SET {', '.join(fields)} WHERE id=${idx} "
            f"RETURNING id, username, email, full_name, bio, email_verified, telegram_chat_id, referral_code, referral_count, (avatar_mime IS NOT NULL) AS has_avatar"
        )
        row = await conn.fetchrow(query, *values)

    return {"user": public_profile(dict(row))}


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Faqat JPEG, PNG yoki WEBP formatdagi rasm yuklang")

    raw = await file.read()
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Rasm hajmi 5MB dan oshmasin")

    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        img.thumbnail((512, 512))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        avatar_bytes = buf.getvalue()
    except Exception:
        raise HTTPException(status_code=400, detail="Rasm faylini o'qib bo'lmadi")

    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE users SET avatar_data=$1, avatar_mime='image/jpeg' WHERE id=$2",
            avatar_bytes, current_user["id"]
        )

    return {"avatar_url": f"/api/auth/avatar/{current_user['username']}"}


@router.get("/avatar/{username}")
async def get_avatar(username: str):
    db = await get_pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT avatar_data, avatar_mime FROM users WHERE username=$1",
            username.strip().lower()
        )
    if not row or not row["avatar_data"]:
        raise HTTPException(status_code=404, detail="Avatar topilmadi")
    return Response(content=bytes(row["avatar_data"]), media_type=row["avatar_mime"])


@router.get("/referral-stats")
async def referral_stats(current_user: dict = Depends(get_current_user)):
    return {
        "referral_code": current_user.get("referral_code"),
        "referral_count": current_user.get("referral_count", 0)
    }
