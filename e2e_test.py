import asyncio, os
os.environ["JWT_SECRET"] = "test-secret-abcdefghijklmnopqrstuvwxyz0123456789"
os.environ["ADMIN_SECRET"] = "admin-secret-abcdefghijklmnopqrstuvwxyz0123456789"
os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost/mockss_test"

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)
client.__enter__()
ADMIN_H = {"X-Admin-Secret": os.environ["ADMIN_SECRET"]}

def check(label, cond):
    print(("OK  " if cond else "FAIL"), label)
    assert cond, label

# 1) super-admin markaz yaratadi
r = client.post("/api/admin/centers", json={"name": "Test Markaz"}, headers=ADMIN_H)
check("center create -> 200", r.status_code == 200)
center_id = r.json()["id"]

# 2) bir oddiy user register qiladi (bo'lajak head-teacher)
r = client.post("/api/auth/register", json={
    "username": "boss1", "email": "boss1@test.com", "full_name": "Bosh Domla", "password": "password123"
})
check("head-teacher candidate register -> 200", r.status_code == 200)
boss_id = r.json()["user"]["id"]

# 3) super-admin uni head-teacher qilib tayinlaydi
r = client.post(f"/api/admin/centers/{center_id}/assign-head-teacher", json={"user_id": boss_id}, headers=ADMIN_H)
check("assign head-teacher -> 200", r.status_code == 200)

# 4) head-teacher login qiladi
r = client.post("/api/auth/login", json={"login": "boss1", "password": "password123"})
check("head-teacher login -> 200", r.status_code == 200)
boss_token = r.json()["token"]
check("role == head_teacher in response", r.json()["user"]["role"] == "head_teacher")
HT_H = {"Authorization": f"Bearer {boss_token}"}

# 5) head-teacher guruh yaratadi
r = client.post("/api/head-teacher/groups", json={"name": "Guruh A"}, headers=HT_H)
check("create group A -> 200", r.status_code == 200)
group_a = r.json()
r2 = client.post("/api/head-teacher/groups", json={"name": "Guruh B"}, headers=HT_H)
group_b = r2.json()
check("create group B -> 200", r2.status_code == 200)

# 6) teacher-invite generatsiya qiladi (Guruh A uchun)
r = client.post(f"/api/head-teacher/groups/{group_a['id']}/generate-teacher-invite", headers=HT_H)
check("generate teacher invite -> 200", r.status_code == 200)
teacher_invite = r.json()["teacher_invite_code"]

# 7) shu link bilan teacher ro'yxatdan o'tadi
r = client.post("/api/auth/register", json={
    "username": "teacher1", "email": "teacher1@test.com", "full_name": "O'qituvchi Ali",
    "password": "password123", "teacher_invite_code": teacher_invite
})
check("teacher self-register -> 200", r.status_code == 200)
check("role == teacher", r.json()["user"]["role"] == "teacher")
teacher_token = r.json()["token"]
T_H = {"Authorization": f"Bearer {teacher_token}"}

# 8) RACE CONDITION TEST: xuddi shu invite code bilan yana ro'yxatdan o'tishga urinish
r = client.post("/api/auth/register", json={
    "username": "teacher2", "email": "teacher2@test.com", "full_name": "Boshqa domla",
    "password": "password123", "teacher_invite_code": teacher_invite
})
check("REUSED teacher invite -> 400 (bloklanishi kerak)", r.status_code == 400)

# 9) CSV bulk import (roster) — Guruh B uchun
csv_content = "email,full_name\nstudent1@test.com,Talaba Bir\nstudent2@test.com,Talaba Ikki\n"
r = client.post(
    f"/api/head-teacher/groups/{group_b['id']}/import-students",
    files={"file": ("students.csv", csv_content, "text/csv")},
    headers=HT_H
)
check("csv import -> 200", r.status_code == 200)
check("csv added == 2", r.json()["added"] == 2)

# 10) roster'da bo'lmagan email bilan Guruh B'ga register qilishga urinish -> 403
r = client.post("/api/auth/register", json={
    "username": "outsider", "email": "outsider@test.com", "full_name": "Notanish",
    "password": "password123", "group_invite_code": group_b["invite_code"]
})
check("non-roster email -> 403", r.status_code == 403)

# 11) roster'dagi email bilan register qilish -> muvaffaqiyatli
r = client.post("/api/auth/register", json={
    "username": "student1", "email": "student1@test.com", "full_name": "Talaba Bir",
    "password": "password123", "group_invite_code": group_b["invite_code"]
})
check("roster email -> 200", r.status_code == 200)

# 12) Guruh A'ga (roster'siz, ochiq invite_code) erkin qo'shilish hali ham ishlashi kerak
r = client.post("/api/auth/register", json={
    "username": "student_open", "email": "student_open@test.com", "full_name": "Ochiq Talaba",
    "password": "password123", "group_invite_code": group_a["invite_code"]
})
check("open-join group A (no roster) -> 200", r.status_code == 200)

# 13) IDOR test: Guruh A teacher'i Guruh B talabasini ID orqali ko'rishga urinadi
r = client.get("/api/teacher/students", headers=T_H)
check("teacher/students -> 200", r.status_code == 200)
own_students = r.json()
check("teacher A sees only group A student", len(own_students) == 1 and own_students[0]["email"] == "student_open@test.com")

r = client.get("/api/teacher/students/999999", headers=T_H)  # mavjud bo'lmagan/boshqa guruh talabasi
check("cross-group student id -> 404", r.status_code == 404)

# 14) head-teacher markazlararo IDOR: boshqa markaz yaratib, uning guruhini ko'rishga urinish
r = client.post("/api/admin/centers", json={"name": "Boshqa Markaz"}, headers=ADMIN_H)
other_center_id = r.json()["id"]
r = client.post("/api/auth/register", json={
    "username": "boss2", "email": "boss2@test.com", "full_name": "Ikkinchi Domla", "password": "password123"
})
boss2_id = r.json()["user"]["id"]
client.post(f"/api/admin/centers/{other_center_id}/assign-head-teacher", json={"user_id": boss2_id}, headers=ADMIN_H)
r = client.post("/api/auth/login", json={"login": "boss2", "password": "password123"})
boss2_token = r.json()["token"]
HT2_H = {"Authorization": f"Bearer {boss2_token}"}

r = client.post(f"/api/head-teacher/groups/{group_a['id']}/deactivate", headers=HT2_H)
check("cross-center group deactivate -> 404", r.status_code == 404)

# 15) remove-teacher va qayta invite generatsiya qilish ishlashi
r = client.post(f"/api/head-teacher/groups/{group_a['id']}/remove-teacher", headers=HT_H)
check("remove-teacher -> 200", r.status_code == 200)
r = client.post(f"/api/head-teacher/groups/{group_a['id']}/generate-teacher-invite", headers=HT_H)
check("re-generate invite after removal -> 200", r.status_code == 200)

# 16) demote qilingan teacher endi teacher endpointiga kira olmasligi kerak
r = client.get("/api/teacher/group", headers=T_H)
check("demoted teacher -> 403", r.status_code == 403)

print("\nBARCHA TESTLAR MUVAFFAQIYATLI O'TDI")
