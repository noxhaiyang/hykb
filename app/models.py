from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class Region(str, Enum):
    domestic = "domestic"
    overseas = "overseas"
    unknown = "unknown"


class EventType(str, Enum):
    update = "更新"
    release = "上线"
    test = "测试"


@dataclass(frozen=True)
class TimelineEvent:
    game_name: str
    event_date: date
    event_time: Optional[str]  # "HH:MM" or None
    region: Region
    event_type: EventType
    tags: str  # comma-separated
    reservation_count: Optional[int]
    raw_text: str
    source_url: str

