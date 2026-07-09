"""IELTS band-score utilities for IELTS Mock SS.

This file can replace the current scoring.py. It fixes two important issues:
1) Listening and Reading now use separate conversion tables.
2) A score of 0 is handled correctly instead of returning None.

Note: IELTS raw-score conversion can vary slightly by test version. Keep the
ranges below aligned with the official/teacher-approved table you want to use.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Optional


# Common IELTS Listening raw-score conversion, out of 40.
LISTENING_BANDS: list[tuple[int, int, float]] = [
    (39, 40, 9.0),
    (37, 38, 8.5),
    (35, 36, 8.0),
    (32, 34, 7.5),
    (30, 31, 7.0),
    (26, 29, 6.5),
    (23, 25, 6.0),
    (18, 22, 5.5),
    (16, 17, 5.0),
    (13, 15, 4.5),
    (11, 12, 4.0),
    (8, 10, 3.5),
    (6, 7, 3.0),
    (4, 5, 2.5),
    (2, 3, 2.0),
    (1, 1, 1.0),
    (0, 0, 0.0),
]


# Common IELTS Academic Reading raw-score conversion, out of 40.
ACADEMIC_READING_BANDS: list[tuple[int, int, float]] = [
    (39, 40, 9.0),
    (37, 38, 8.5),
    (35, 36, 8.0),
    (33, 34, 7.5),
    (30, 32, 7.0),
    (27, 29, 6.5),
    (23, 26, 6.0),
    (19, 22, 5.5),
    (15, 18, 5.0),
    (13, 14, 4.5),
    (10, 12, 4.0),
    (8, 9, 3.5),
    (6, 7, 3.0),
    (4, 5, 2.5),
    (2, 3, 2.0),
    (1, 1, 1.0),
    (0, 0, 0.0),
]


# Optional: IELTS General Training Reading is stricter than Academic Reading.
GENERAL_READING_BANDS: list[tuple[int, int, float]] = [
    (40, 40, 9.0),
    (39, 39, 8.5),
    (37, 38, 8.0),
    (36, 36, 7.5),
    (34, 35, 7.0),
    (32, 33, 6.5),
    (30, 31, 6.0),
    (27, 29, 5.5),
    (23, 26, 5.0),
    (19, 22, 4.5),
    (15, 18, 4.0),
    (12, 14, 3.5),
    (9, 11, 3.0),
    (6, 8, 2.5),
    (3, 5, 2.0),
    (1, 2, 1.0),
    (0, 0, 0.0),
]


def _normalise_to_40(score: int | float | None, total: int | float | None) -> Optional[int]:
    """Convert any section score to an equivalent score out of 40."""
    if score is None or total is None:
        return None

    try:
        score_num = float(score)
        total_num = float(total)
    except (TypeError, ValueError):
        return None

    if total_num <= 0:
        return None

    score_num = max(0.0, min(score_num, total_num))
    return int(round((score_num / total_num) * 40))


def _lookup_band(raw_out_of_40: int, table: list[tuple[int, int, float]]) -> float:
    raw_out_of_40 = max(0, min(int(raw_out_of_40), 40))
    for low, high, band in table:
        if low <= raw_out_of_40 <= high:
            return band
    return 0.0


def get_band_score(
    score: int | float | None,
    total: int | float | None,
    section: str,
    reading_module: str = "academic",
) -> Optional[float]:
    """Return IELTS band for Listening or Reading.

    Args:
        score: Number of correct answers.
        total: Total number of questions. Usually 40.
        section: listening, reading, writing, or speaking.
        reading_module: academic or general. Default is academic.
    """
    section_key = (section or "").strip().lower()
    if section_key in {"writing", "speaking"}:
        return None

    raw = _normalise_to_40(score, total)
    if raw is None:
        return None

    if section_key == "listening":
        return _lookup_band(raw, LISTENING_BANDS)

    if section_key == "reading":
        table = GENERAL_READING_BANDS if reading_module.lower().startswith("general") else ACADEMIC_READING_BANDS
        return _lookup_band(raw, table)

    return None


def round_ielts_overall(average: float) -> float:
    """Round an IELTS average to the nearest whole or half band.

    IELTS overall rounding convention:
    - .25 rounds up to .5
    - .75 rounds up to the next whole band
    """
    value = Decimal(str(average))
    whole = value.to_integral_value(rounding=ROUND_FLOOR)
    fraction = value - whole

    if fraction < Decimal("0.25"):
        return float(whole)
    if fraction < Decimal("0.75"):
        return float(whole + Decimal("0.5"))
    return float(whole + Decimal("1.0"))


def calculate_overall_band(section_bands: Iterable[float | int | None]) -> Optional[float]:
    valid = [float(band) for band in section_bands if band is not None]
    if not valid:
        return None
    return round_ielts_overall(sum(valid) / len(valid))
