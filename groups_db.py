"""
Markaz (center) / Guruh (group) / Roster jadvallari va yordamchi funksiyalar.
ensure_users_table() bilan bir xil patternda — main.py startup eventida chaqiriladi.
"""

from db import get_pool

DEFAULT_MAX_GROUPS_PER_CENTER = 10
DEFAULT_MAX_STUDENTS_PER_CENTER = 500


async def ensure_center_group_tables():
    db = await get_pool()
    async with db.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS centers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                max_groups INTEGER,
                max_students INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                teacher_invite_code TEXT UNIQUE,
                teacher_invite_created_at TIMESTAMPTZ,
                teacher_invite_expires_at TIMESTAMPTZ,
                teacher_invite_revoked_at TIMESTAMPTZ,
                teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                center_id INTEGER REFERENCES centers(id) ON DELETE CASCADE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )

        # Eski bazalar uchun xavfsiz, idempotent migratsiyalar.
        await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS teacher_invite_created_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS teacher_invite_expires_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS teacher_invite_revoked_at TIMESTAMPTZ")
        # Deploydan oldin yaratilgan muddatsiz takliflarga ham 48 soatlik o'tish muddati beradi.
        await conn.execute(
            """
            UPDATE groups
            SET teacher_invite_created_at = COALESCE(teacher_invite_created_at, NOW()),
                teacher_invite_expires_at = NOW() + INTERVAL '48 hours'
            WHERE teacher_invite_code IS NOT NULL
              AND teacher_invite_expires_at IS NULL
            """
        )

        await conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'groups_teacher_id_unique'
                ) THEN
                    ALTER TABLE groups
                    ADD CONSTRAINT groups_teacher_id_unique UNIQUE (teacher_id);
                END IF;
            END $$;
            """
        )

        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'student'")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id) ON DELETE SET NULL")

        await conn.execute(
            """
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
            """
        )

        # Writing baholash mezonlari va audit maydonlari.
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_task_achievement NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_coherence_cohesion NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_lexical_resource NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_grammar_accuracy NUMERIC")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_graded_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        await conn.execute("ALTER TABLE exam_results ADD COLUMN IF NOT EXISTS writing_graded_at TIMESTAMP")

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_groups_teacher_invite_code ON groups(teacher_invite_code)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exam_results_email_submitted ON exam_results(email, submitted_at DESC)"
        )

        # auth.py teacher tokenni UPDATE ... SET teacher_invite_code=NULL orqali atomik iste'mol qiladi.
        # Ushbu trigger eskirgan tokenning o'sha UPDATE'ini jimgina bekor qiladi. Natijada auth.py
        # mavjud "noto'g'ri yoki ishlatilgan" xabarini qaytaradi va auth.py faylini o'zgartirish shart emas.
        await conn.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_expired_teacher_invite_consumption()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.teacher_invite_code IS NOT NULL
                   AND NEW.teacher_invite_code IS NULL
                   AND NEW.teacher_invite_expires_at IS NOT DISTINCT FROM OLD.teacher_invite_expires_at
                   AND OLD.teacher_invite_expires_at IS NOT NULL
                   AND OLD.teacher_invite_expires_at <= NOW()
                THEN
                    RETURN NULL;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        await conn.execute("DROP TRIGGER IF EXISTS trg_prevent_expired_teacher_invite ON groups")
        await conn.execute(
            """
            CREATE TRIGGER trg_prevent_expired_teacher_invite
            BEFORE UPDATE OF teacher_invite_code ON groups
            FOR EACH ROW
            EXECUTE FUNCTION prevent_expired_teacher_invite_consumption()
            """
        )


async def get_center_limits(conn, center_id: int):
    """Markaz uchun effektiv (max_groups, max_students) qiymatlarini qaytaradi."""
    row = await conn.fetchrow(
        "SELECT max_groups, max_students FROM centers WHERE id=$1", center_id
    )
    max_groups = (
        row["max_groups"]
        if row and row["max_groups"]
        else DEFAULT_MAX_GROUPS_PER_CENTER
    )
    max_students = (
        row["max_students"]
        if row and row["max_students"]
        else DEFAULT_MAX_STUDENTS_PER_CENTER
    )
    return max_groups, max_students
