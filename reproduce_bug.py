import asyncio
import json
from db import get_pool
from auth import hash_password, create_token
from main import app
from httpx import AsyncClient

async def reproduce():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. Tozalash
        await conn.execute("DELETE FROM groups")
        await conn.execute("DELETE FROM users")
        await conn.execute("DELETE FROM centers")
        
        # 2. Center va Group yaratish
        center_id = await conn.fetchval("INSERT INTO centers (name) VALUES ('Test Center') RETURNING id")
        group_id = await conn.fetchval("INSERT INTO groups (name, invite_code, teacher_invite_code, center_id) VALUES ('Test Group', 'GI123', 'TI123', ) RETURNING id", center_id)
        
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
        print(f"Register response: {resp.status_code}, {resp.json()}")
        
        token = resp.json()["token"]
        user = resp.json()["user"]
        
        # 4. Teacher login qilishi
        login_data = {"login": "teacher_test", "password": "password123"}
        resp = await ac.post("/api/auth/login", json=login_data)
        print(f"Login response: {resp.status_code}, {resp.json()}")
        
        # 5. Teacher group API ga murojaat qilishi
        headers = {"Authorization": f"Bearer {token}"}
        resp = await ac.get("/api/teacher/group", headers=headers)
        print(f"Teacher group response: {resp.status_code}, {resp.json()}")

if __name__ == "__main__":
    asyncio.run(reproduce())
