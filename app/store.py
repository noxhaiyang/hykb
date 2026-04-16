from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .models import TimelineEvent
from .utils import stable_dedupe_key


def default_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "games.db"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT NOT NULL UNIQUE,
          game_name TEXT NOT NULL,
          event_date TEXT NOT NULL,
          event_time TEXT,
          region TEXT NOT NULL,
          event_type TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '',
          reservation_count INTEGER,
          raw_text TEXT NOT NULL,
          source_url TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    # 轻量迁移：老库缺字段时补上
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(timeline_events)").fetchall()}
    if "tags" not in cols:
        conn.execute("ALTER TABLE timeline_events ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
    if "reservation_count" not in cols:
        conn.execute("ALTER TABLE timeline_events ADD COLUMN reservation_count INTEGER")

    # 旧版本使用英文 event_type，且 dedupe_key 计算规则不同，会导致库内重复。
    # 为保证“日历数据干净”，发现旧类型时清空一次重建（下次抓取会自动回填）。
    old_types = ("release", "test", "prereg", "update", "recruit", "other")
    cur = conn.execute(
        f"SELECT COUNT(1) AS c FROM timeline_events WHERE event_type IN ({','.join(['?']*len(old_types))})",
        old_types,
    )
    if (cur.fetchone()["c"] or 0) > 0:
        conn.execute("DELETE FROM timeline_events")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_events_date ON timeline_events(event_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_events_region ON timeline_events(region)"
    )
    conn.commit()


def upsert_events(conn: sqlite3.Connection, events: Sequence[TimelineEvent]) -> dict:
    """
    以 dedupe_key 做 upsert。返回统计信息：inserted/updated/total.
    """
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    inserted = 0
    updated = 0

    for e in events:
        dedupe = stable_dedupe_key(
            e.source_url, e.event_date, e.event_time, e.event_type.value, e.region.value
        )
        row = {
            "dedupe_key": dedupe,
            "game_name": e.game_name,
            "event_date": e.event_date.isoformat(),
            "event_time": e.event_time,
            "region": e.region.value,
            "event_type": e.event_type.value,
            "tags": e.tags or "",
            "reservation_count": e.reservation_count,
            "raw_text": e.raw_text,
            "source_url": e.source_url,
            "created_at": now,
            "updated_at": now,
        }

        cur = conn.execute(
            "SELECT id, raw_text, source_url FROM timeline_events WHERE dedupe_key=?",
            (dedupe,),
        )
        existing = cur.fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO timeline_events (
                  dedupe_key, game_name, event_date, event_time, region, event_type,
                  tags, reservation_count,
                  raw_text, source_url, created_at, updated_at
                ) VALUES (
                  :dedupe_key, :game_name, :event_date, :event_time, :region, :event_type,
                  :tags, :reservation_count,
                  :raw_text, :source_url, :created_at, :updated_at
                )
                """,
                row,
            )
            inserted += 1
        else:
            # 仅在内容有变化时更新（避免每天跑导致 updated_at 全刷）
            cur2 = conn.execute(
                "SELECT raw_text, source_url, tags, reservation_count FROM timeline_events WHERE dedupe_key=?",
                (dedupe,),
            )
            ex2 = cur2.fetchone()
            if ex2 is None:
                continue
            if (
                ex2["raw_text"] != row["raw_text"]
                or ex2["source_url"] != row["source_url"]
                or (ex2["tags"] or "") != (row["tags"] or "")
                or (ex2["reservation_count"] if ex2["reservation_count"] is not None else None)
                != row["reservation_count"]
            ):
                conn.execute(
                    """
                    UPDATE timeline_events
                    SET raw_text=:raw_text,
                        source_url=:source_url,
                        tags=:tags,
                        reservation_count=:reservation_count,
                        updated_at=:updated_at
                    WHERE dedupe_key=:dedupe_key
                    """,
                    {
                        "raw_text": row["raw_text"],
                        "source_url": row["source_url"],
                        "tags": row["tags"],
                        "reservation_count": row["reservation_count"],
                        "updated_at": now,
                        "dedupe_key": dedupe,
                    },
                )
                updated += 1

    conn.commit()
    return {"inserted": inserted, "updated": updated, "total": len(events)}


def replace_all_events(conn: sqlite3.Connection, events: Sequence[TimelineEvent]) -> dict:
    """
    全量替换 timeline_events（以当前页面快照为准）。
    用于规则调整后的数据重建，避免历史规则残留干扰结果。
    """
    conn.execute("DELETE FROM timeline_events")
    stats = upsert_events(conn, events)
    stats["replaced_all"] = 1
    return stats


def cleanup_duplicate_source_events(conn: sqlite3.Connection) -> int:
    """
    清理历史重复：同一详情链接、同一事件类型、同一事件日出现多条时仅保留一条
    （时间线多个区块重复挂载同一条目）；不同事件日（如预下载日 vs 上线日）互不合并。
    """
    sql = """
    WITH ranked AS (
      SELECT
        id,
        ROW_NUMBER() OVER (
          PARTITION BY source_url, event_type, event_date
          ORDER BY COALESCE(event_time, '99:99') ASC, id ASC
        ) AS rn
      FROM timeline_events
      WHERE source_url LIKE '%/a/%'
    )
    DELETE FROM timeline_events
    WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """
    before = conn.execute("SELECT COUNT(1) AS c FROM timeline_events").fetchone()["c"]
    conn.execute(sql)
    after = conn.execute("SELECT COUNT(1) AS c FROM timeline_events").fetchone()["c"]
    conn.commit()
    return int(before - after)


def query_events(
    conn: sqlite3.Connection,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    region: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 500,
) -> List[sqlite3.Row]:
    wheres = []
    params: list = []

    if start:
        wheres.append("event_date >= ?")
        params.append(start)
    if end:
        wheres.append("event_date <= ?")
        params.append(end)
    if region and region != "all":
        wheres.append("region = ?")
        params.append(region)
    if q:
        wheres.append("(game_name LIKE ? OR raw_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])

    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    cur = conn.execute(
        f"""
        SELECT game_name, event_date, event_time, region, event_type, tags, reservation_count, raw_text, source_url, updated_at
        FROM timeline_events
        {where_sql}
        ORDER BY event_date ASC, COALESCE(event_time, '') ASC, game_name ASC, id ASC
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    return list(cur.fetchall())

