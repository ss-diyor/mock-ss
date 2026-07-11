import asyncio
import os
import json
from db import get_pool
from auth import hash_password, create_token
from main import app
from httpx import AsyncClient

# JWT_SECRET ni o'rnatish
os.environ["JWT_SECRET"] = "super-secret-key-that-is-at-least-32-characters-long"

async def test_flow():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Tozalash
        await conn.execute("DELETE FROM groups")
        await conn.execute("DELETE FROM users")
        await conn.execute("DELETE FROM centers")
        
        # 2. Center va Group yaratish
        center_id = await conn.fetchval("INSERT INTO centers (name) VALUES ('Test Center') RETURNING id")
        group_id = await conn.fetchval("INSERT INTO groups (name, invite_code, teacher_invite_code, center_id) VALUES ('Test Group', 'GI123', 'TI123', $1) RETURNING id", center_id)
        
        print(f"Created center {center_id} and group {group_id}")

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # 3. Teacher ro'yxatdan o'tishi
        reg_data = {
            "username": "teacher_test",
            "email": "teacher@test.com",
            "full_name": "Test Teacher",
            "password": "password123",
            "teacher_invite_code": "TI123"
        }
        resp = await ac.post("/api/auth/register", json=reg_data)
        print(f"Register response: {resp.status_code}")
        
        token = resp.json()["token"]
        
        # 4. DB dagi holatni tekshirish
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE username='teacher_test'")
            print(f"User in DB: id={user['id']}, role={user['role']}, group_id={user['group_id']}, center_id={user['center_id']}")
            
            group = await conn.fetchrow("SELECT * FROM groups WHERE id=$1", group_id)
            print(f"Group in DB: id={group['id']}, teacher_id={group['teacher_id']}, teacher_invite_code={group['teacher_invite_code']}")

        # 5. Teacher group API ga murojaat qilishi
        headers = {"Authorization": f"Bearer {token}"}
        resp = await ac.get("/api/teacher/group", headers=headers)
        print(f"Teacher group response: {resp.status_code}, {resp.json()}")

if __name__ == "__main__":
    # Mocking DATABASE_URL if needed, but assuming it's already in env or handled by get_pool
    asyncio.run(test_flow())
