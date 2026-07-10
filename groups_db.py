"""
Markaz (center) / Guruh (group) / Roster jadvallari va yordamchi funksiyalar.
ensure_users_table() bilan bir xil patternda — main.py startup eventida chaqiriladi.
"""

from db import get_pool

# Hozircha barcha markazlar uchun global default limitlar.
# centers.max_groups / centers.max_students ustunlari orqali har bir markaz
# uchun alohida qiymat o'rnatilishi mumkin (NULL bo'lsa — shu default ishlatiladi).
DEFAULT_MAX_GROUPS_PER_CENTER = 10
DEFAULT_MAX_STUDENTS_PER_CENTER = 500


async def ensure_center_group_tables():
    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS centers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                max_groups INTEGER,
                max_students INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                teacher_invite_code TEXT UNIQUE,
                teacher_invite_expires_at TIMESTAMP,
                teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                center_id INTEGER REFERENCES centers(id) ON DELETE CASCADE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS teacher_invite_expires_at TIMESTAMP")


        # PostgreSQL "ADD CONSTRAINT IF NOT EXISTS"ni qo'llab-quvvatlamaydi,
        # shuning uchun pg_constraint katalogini o'zimiz tekshiramiz.
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'groups_teacher_id_unique'
                ) THEN
                    ALTER TABLE groups ADD CONSTRAINT groups_teacher_id_unique UNIQUE (teacher_id);
                END IF;
            END $$;
        """)

        # users jadvaliga rol/guruh/markaz ustunlari
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'student'")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id) ON DELETE SET NULL")

        # Bulk CSV import orqali oldindan qo'shiladigan email ro'yxati ("roster").
        # Agar guruh uchun bu jadvalda qatorlar bo'lsa, register paytida
        # group_invite_code bilan qo'shilayotgan email shu ro'yxatda bo'lishi shart.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS group_roster_emails (
                id SERIAL PRIMARY KEY,
                group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                full_name TEXT,
                used BOOLEAN DEFAULT FALSE,
                used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(group_id, email)
            )
        """)


async def get_center_limits(conn, center_id: int):
    """Markaz uchun effektiv (max_groups, max_students) qiymatlarini qaytaradi.
    Markazda alohida qiymat o'rnatilmagan bo'lsa — global default ishlatiladi."""
    row = await conn.fetchrow("SELECT max_groups, max_students FROM centers WHERE id=$1", center_id)
    max_groups = (row["max_groups"] if row and row["max_groups"] else DEFAULT_MAX_GROUPS_PER_CENTER)
    max_students = (row["max_students"] if row and row["max_students"] else DEFAULT_MAX_STUDENTS_PER_CENTER)
    return max_groups, max_students
