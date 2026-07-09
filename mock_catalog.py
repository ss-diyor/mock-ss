"""Test catalog for IELTS Mock SS.

This module gives the frontend and backend one shared source of truth for demo
mock sets. Add real tests here as your content bank grows.
"""

from __future__ import annotations

from typing import Any


TEST_CATALOG: list[dict[str, Any]] = [
    {
        "id": "mock-001",
        "title": "IELTS Mock Test 1",
        "level": "Academic",
        "status": "ready",
        "description": "Demo uchun asosiy Listening, Reading va Writing bo'limlari.",
        "sections": [
            {"key": "listening", "title": "Listening", "duration_minutes": 30, "questions": 40, "route": "/listening-demo?test=mock-001"},
            {"key": "reading", "title": "Reading", "duration_minutes": 60, "questions": 40, "route": "/reading-demo?test=mock-001"},
            {"key": "writing", "title": "Writing", "duration_minutes": 60, "questions": 2, "route": "/writing-demo?test=mock-001"},
            {"key": "speaking", "title": "Speaking", "duration_minutes": 15, "questions": 3, "route": "/speaking-demo?test=mock-001"},
        ],
    },
    {
        "id": "mock-002",
        "title": "IELTS Mock Test 2",
        "level": "Academic",
        "status": "demo",
        "description": "Ta'lim markazi demo kuni uchun qo'shimcha test set.",
        "sections": [
            {"key": "listening", "title": "Listening", "duration_minutes": 30, "questions": 40, "route": "/listening-demo?test=mock-002"},
            {"key": "reading", "title": "Reading", "duration_minutes": 60, "questions": 40, "route": "/reading-demo?test=mock-002"},
            {"key": "writing", "title": "Writing", "duration_minutes": 60, "questions": 2, "route": "/writing-demo?test=mock-002"},
            {"key": "speaking", "title": "Speaking", "duration_minutes": 15, "questions": 3, "route": "/speaking-demo?test=mock-002"},
        ],
    },
    {
        "id": "mock-003",
        "title": "IELTS Mock Test 3",
        "level": "Academic",
        "status": "planned",
        "description": "Real test-bank qo'shilgandan keyin faollashtiriladi.",
        "sections": [
            {"key": "listening", "title": "Listening", "duration_minutes": 30, "questions": 40, "route": "/listening-demo?test=mock-003"},
            {"key": "reading", "title": "Reading", "duration_minutes": 60, "questions": 40, "route": "/reading-demo?test=mock-003"},
            {"key": "writing", "title": "Writing", "duration_minutes": 60, "questions": 2, "route": "/writing-demo?test=mock-003"},
            {"key": "speaking", "title": "Speaking", "duration_minutes": 15, "questions": 3, "route": "/speaking-demo?test=mock-003"},
        ],
    },
]


def get_test_by_id(test_id: str) -> dict[str, Any] | None:
    return next((test for test in TEST_CATALOG if test["id"] == test_id), None)


def public_test_catalog() -> list[dict[str, Any]]:
    """Return a frontend-safe copy of the catalog."""
    return TEST_CATALOG
