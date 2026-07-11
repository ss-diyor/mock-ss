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
                organization_type TEXT NOT NULL DEFAULT 'learning_center',
                slug TEXT,
                owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                max_groups INTEGER,
                max_students INTEGER,
                brand_name TEXT,
                brand_primary_color TEXT DEFAULT '#1a56e8',
                brand_secondary_color TEXT DEFAULT '#0b1733',
                brand_logo_url TEXT,
                brand_favicon_url TEXT,
                brand_contact_email TEXT,
                brand_contact_phone TEXT,
                show_powered_by BOOLEAN DEFAULT TRUE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS organization_type TEXT NOT NULL DEFAULT 'learning_center'")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS slug TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_name TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_primary_color TEXT DEFAULT '#1a56e8'")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_secondary_color TEXT DEFAULT '#0b1733'")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_logo_url TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_favicon_url TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_contact_email TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS brand_contact_phone TEXT")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS show_powered_by BOOLEAN DEFAULT TRUE")
        await conn.execute("ALTER TABLE centers ADD COLUMN IF NOT EXISTS subscription_required BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS centers_slug_unique ON centers (LOWER(slug)) WHERE slug IS NOT NULL")

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

        # Maktablar uchun moslashuvchan lavozim, xodim va sinf modeli.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_positions (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_system BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(center_id, name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_staff (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                position_id INTEGER REFERENCES school_positions(id) ON DELETE SET NULL,
                employee_code TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(center_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_classes (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                grade_level INTEGER,
                homeroom_staff_id INTEGER REFERENCES school_staff(id) ON DELETE SET NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(center_id, name, academic_year)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_class_students (
                id SERIAL PRIMARY KEY,
                class_id INTEGER NOT NULL REFERENCES school_classes(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                joined_at TIMESTAMP DEFAULT NOW(),
                left_at TIMESTAMP,
                UNIQUE(class_id, student_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS school_staff_center_idx ON school_staff(center_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS school_classes_center_idx ON school_classes(center_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS school_class_students_student_idx ON school_class_students(student_id)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_subjects (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                code TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(center_id, name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS school_teacher_assignments (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                class_id INTEGER NOT NULL REFERENCES school_classes(id) ON DELETE CASCADE,
                subject_id INTEGER NOT NULL REFERENCES school_subjects(id) ON DELETE CASCADE,
                staff_id INTEGER NOT NULL REFERENCES school_staff(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(class_id, subject_id, staff_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS school_assignments_staff_idx ON school_teacher_assignments(staff_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS school_assignments_class_idx ON school_teacher_assignments(class_id)")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                price_monthly BIGINT NOT NULL,
                price_yearly BIGINT NOT NULL,
                duration_months INTEGER NOT NULL DEFAULT 1,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS organization_subscriptions (
                id SERIAL PRIMARY KEY,
                center_id INTEGER UNIQUE NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                plan_id INTEGER REFERENCES subscription_plans(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                trial_ends_at TIMESTAMP,
                current_period_start TIMESTAMP,
                current_period_end TIMESTAMP,
                grace_ends_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscription_payments (
                id SERIAL PRIMARY KEY,
                center_id INTEGER NOT NULL REFERENCES centers(id) ON DELETE CASCADE,
                plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
                billing_cycle TEXT NOT NULL,
                amount BIGINT NOT NULL,
                order_code TEXT UNIQUE NOT NULL,
                payer_name TEXT NOT NULL,
                transaction_reference TEXT UNIQUE,
                receipt_data BYTEA NOT NULL,
                receipt_mime TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                review_note TEXT,
                reviewed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS subscription_payments_status_idx ON subscription_payments(status, created_at)")
        await conn.execute("""
            INSERT INTO subscription_plans(code, name, price_monthly, price_yearly, duration_months)
            VALUES
              ('start', 'Start', 299000, 2990000, 1),
              ('growth', 'Growth', 699000, 6990000, 1),
              ('pro', 'Pro', 1490000, 14900000, 1)
            ON CONFLICT(code) DO NOTHING
        """)


async def get_center_limits(conn, center_id: int):
    """Markaz uchun effektiv (max_groups, max_students) qiymatlarini qaytaradi.
    Markazda alohida qiymat o'rnatilmagan bo'lsa — global default ishlatiladi."""
    row = await conn.fetchrow("SELECT max_groups, max_students FROM centers WHERE id=$1", center_id)
    max_groups = (row["max_groups"] if row and row["max_groups"] else DEFAULT_MAX_GROUPS_PER_CENTER)
    max_students = (row["max_students"] if row and row["max_students"] else DEFAULT_MAX_STUDENTS_PER_CENTER)
    return max_groups, max_students
