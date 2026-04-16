from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Mapping


def build_dashboard(rows: list[Mapping[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    """Build dashboard metrics for UI and reports."""
    today = today or date.today()
    today_s = today.isoformat()
    total = len(rows)

    overseas = sum(1 for r in rows if r["region"] == "overseas")
    domestic = total - overseas

    next7_end = (today + timedelta(days=7)).isoformat()
    next14_end = (today + timedelta(days=14)).isoformat()
    next7 = sum(1 for r in rows if today_s <= r["event_date"] <= next7_end)
    next7_after = sum(1 for r in rows if next7_end < r["event_date"] <= next14_end)
    today_cnt = sum(1 for r in rows if r["event_date"] == today_s)

    release_cnt = sum(1 for r in rows if r["event_type"] == "上线")
    test_cnt = sum(1 for r in rows if r["event_type"] == "测试")
    predownload_cnt = sum(1 for r in rows if r["event_type"] == "预下载")
    missing_time = sum(1 for r in rows if not (r["event_time"] or "").strip())

    duplicate_key = Counter((r["game_name"], r["event_date"], r["event_type"]) for r in rows)
    duplicate_groups = sum(1 for _, c in duplicate_key.items() if c > 1)

    date_counter = Counter(r["event_date"] for r in rows)
    conflict_days = sorted(
        [(d, c) for d, c in date_counter.items() if c >= 6],
        key=lambda x: (-x[1], x[0]),
    )[:5]

    completeness_rate = round((1.0 - missing_time / total) * 100.0, 1) if total else 100.0
    missing_time_rate = round((missing_time / total * 100.0), 1) if total else 0.0
    overseas_rate = round((overseas / total * 100.0), 1) if total else 0.0
    next7_delta = next7 - next7_after
    next7_delta_pct = round((next7_delta / next7_after) * 100.0, 1) if next7_after else 0.0

    return {
        "total": total,
        "overseas": overseas,
        "domestic": domestic,
        "overseas_rate": overseas_rate,
        "next7": next7,
        "next7_after": next7_after,
        "next7_delta": next7_delta,
        "next7_delta_pct": next7_delta_pct,
        "today_cnt": today_cnt,
        "release_cnt": release_cnt,
        "test_cnt": test_cnt,
        "predownload_cnt": predownload_cnt,
        "missing_time_rate": missing_time_rate,
        "completeness_rate": completeness_rate,
        "duplicate_groups": duplicate_groups,
        "conflict_days": conflict_days,
    }
