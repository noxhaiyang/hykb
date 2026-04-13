from __future__ import annotations

import argparse
from datetime import date
from typing import Optional

import requests

from .parser import parse_timeline_html
from .store import cleanup_duplicate_source_events, connect, init_db, upsert_events
from .utils import request_with_retry


TIMELINE_URL = "https://www.3839.com/timeline.html"


def fetch_timeline_html(url: str = TIMELINE_URL) -> str:
    def _do() -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text

    return request_with_retry(_do, retries=3)


def run_once(today: Optional[date] = None) -> dict:
    html = fetch_timeline_html()
    events = parse_timeline_html(html, base_url=TIMELINE_URL, today=today)

    conn = connect()
    try:
        init_db(conn)
        stats = upsert_events(conn, events)
        deleted = cleanup_duplicate_source_events(conn)
        stats["deleted_duplicates"] = deleted
    finally:
        conn.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.crawler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="抓取时间线并写入SQLite")
    run_p.add_argument("--today", help="用于调试的 today，格式 YYYY-MM-DD", default=None)

    args = parser.parse_args()

    if args.cmd == "run":
        today = date.fromisoformat(args.today) if args.today else None
        stats = run_once(today=today)
        print(
            f"crawler done. inserted={stats['inserted']} updated={stats['updated']} "
            f"deleted_duplicates={stats.get('deleted_duplicates', 0)} total_parsed={stats['total']}"
        )


if __name__ == "__main__":
    main()

