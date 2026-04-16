from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple, TypeVar

from .models import EventType, Region

T = TypeVar("T")

MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日")
# 快爆标题常见「4.17正式上线」；日须两位数以减少与评分「5.7」等混淆；左侧禁止紧贴数字以免误匹配「2026.04」
DOT_MONTH_DAY_RE = re.compile(
    r"(?<![0-9])(?P<month>1[0-2]|[1-9])\.(?P<day>0[1-9]|[12][0-9]|3[01])(?![0-9])"
)
TIME_RE = re.compile(r"\b(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)\b")


def stable_dedupe_key(
    source_url: str,
    event_date: date,
    event_time: Optional[str],
    event_type: str,
    region: str,
) -> str:
    base = "|".join(
        [
            normalize_space(source_url).lower(),
            event_date.isoformat(),
            (event_time or "").strip(),
            (event_type or "").strip().lower(),
            (region or "").strip().lower(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _date_from_month_day(month: int, day: int, *, today: date) -> date:
    guessed = date(today.year, month, day)
    # 跨年修正：如果今天在 1月，遇到 12月则认为是去年；反之亦然
    if today.month == 1 and month == 12:
        guessed = date(today.year - 1, month, day)
    elif today.month == 12 and month == 1:
        guessed = date(today.year + 1, month, day)
    return guessed


def parse_month_day_to_date(
    month_day_text: str, *, today: Optional[date] = None
) -> Optional[date]:
    """
    将 '04月07日 今天/明天/上周二' 这类文本解析为具体日期。
    默认以当前年份推断；对跨年做轻微修正（例如 12月 + 在 1月附近）。 
    """
    m = MONTH_DAY_RE.search(month_day_text)
    if not m:
        return None

    today = today or date.today()
    return _date_from_month_day(int(m.group("month")), int(m.group("day")), today=today)


def parse_month_days_in_text(text: str, *, today: Optional[date] = None) -> List[date]:
    """
    解析文本中全部「M月D日」与「M.DD」片段（按在文中出现顺序）。
    用于条目标题内日期：可覆盖时间线卡片头日期（卡片有时与条目真实日期不一致）。
    """
    today = today or date.today()
    spans: List[tuple[int, date]] = []
    for m in MONTH_DAY_RE.finditer(text or ""):
        d = _date_from_month_day(int(m.group("month")), int(m.group("day")), today=today)
        spans.append((m.start(), d))
    for m in DOT_MONTH_DAY_RE.finditer(text or ""):
        d = _date_from_month_day(int(m.group("month")), int(m.group("day")), today=today)
        spans.append((m.start(), d))
    spans.sort(key=lambda x: x[0])
    return [d for _, d in spans]


def extract_time(text: str) -> Optional[str]:
    m = TIME_RE.search(text or "")
    if not m:
        return None
    return f"{int(m.group('hour')):02d}:{int(m.group('minute')):02d}"


def has_predownload_narrative(text: str) -> bool:
    """
    判断文案是否描述「预下载」这一事件，而非仅商品名里的「-预下载」后缀。
    """
    t = text or ""
    return (
        "预下载已开启" in t
        or "开启预下载" in t
        or "预下载开启" in t
        or ("定档" in t and "预下载" in t)
        or "预下载，" in t
        or "预下载," in t
    )


def guess_region(text: str) -> Region:
    t = text or ""
    if "海外" in t or "国际" in t:
        return Region.overseas
    if "国服" in t or "官服" in t or "国内" in t:
        return Region.domestic
    # 需求：未知地区按“国内”展示/处理
    return Region.domestic


def guess_event_type(text: str) -> EventType:
    t = text or ""
    if "招募" in t:
        return EventType.recruit
    if "测试" in t or "开测" in t:
        return EventType.test
    if has_predownload_narrative(t):
        return EventType.predownload
    if "试玩" in t:
        return EventType.trial
    # 纯上线 / 标题仅有「-预下载」后缀但叙述为正式上线（parser 拆条外的单卡）
    if "正式上线" in t or "上线" in t or "开服" in t:
        return EventType.release
    if "预约" in t or "预购" in t:
        return EventType.reservation
    # 其余统一归为“更新”（含预约/招募/版本等）
    if (
        "版本" in t
        or "更新" in t
        or "新赛季" in t
        or "活动" in t
        or "联动" in t
        or "登场" in t
        or "开启" in t
        or "抢注" in t
    ):
        return EventType.update
    return EventType.update


def request_with_retry(
    fetch: Callable[[], T],
    *,
    retries: int = 3,
    base_sleep_s: float = 0.6,
    max_sleep_s: float = 5.0,
) -> T:
    last_exc: Optional[Exception] = None
    for i in range(retries + 1):
        try:
            return fetch()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if i >= retries:
                break
            sleep_s = min(max_sleep_s, base_sleep_s * (2**i)) + random.random() * 0.2
            time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def parse_date_range(
    start: Optional[str], end: Optional[str]
) -> Tuple[Optional[date], Optional[date]]:
    def parse_one(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        return datetime.strptime(s, "%Y-%m-%d").date()

    s = parse_one(start)
    e = parse_one(end)
    return s, e


def clamp_date_range(
    start: Optional[date], end: Optional[date]
) -> Tuple[Optional[date], Optional[date]]:
    if start and end and start > end:
        return end, start
    return start, end

