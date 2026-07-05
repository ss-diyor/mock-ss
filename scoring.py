def get_band_score(score, total, section):
    if not score or not total or section == "writing":
        return None

    normalized = round((score / total) * 40)

    band_table = {
        40: 9.0, 39: 9.0, 38: 8.5, 37: 8.5,
        36: 8.0, 35: 8.0, 34: 7.5, 33: 7.5,
        32: 7.0, 31: 7.0, 30: 6.5, 29: 6.5,
        28: 6.0, 27: 6.0, 26: 5.5, 25: 5.5,
        24: 5.5, 23: 5.0, 22: 5.0, 21: 5.0,
        20: 4.5, 19: 4.5, 18: 4.0, 17: 4.0,
        16: 4.0, 15: 3.5, 14: 3.5, 13: 3.0,
        12: 3.0, 11: 2.5, 10: 2.5,
    }
    return band_table.get(normalized, 2.0)
