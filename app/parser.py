from __future__ import annotations

import re
from datetime import date
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import EventType, TimelineEvent
from .utils import (
    extract_time,
    guess_event_type,
    guess_region,
    has_predownload_narrative,
    normalize_space,
    parse_month_day_to_date,
    parse_month_days_in_text,
)


_ACTION_TEXTS = {"下载", "预约", "试玩", "关注"}
_EVENT_PRIORITY = {
    EventType.release: 70,
    EventType.predownload: 60,
    EventType.test: 50,
    EventType.recruit: 40,
    EventType.reservation: 30,
    EventType.trial: 20,
    EventType.update: 10,
}


_NAME_SUFFIX_CLEANUPS = [
    "-新版本预约",
    "-新版本",
    "-体验服",
    "-预下载",
    "-近期爆火新游",
    "-全角色&武器免费",
]

# 仅保留时间线页内游戏详情链（不跟随其它列表页/站外链，也不请求详情页）
_TIMELINE_GAME_DETAIL = re.compile(r"//(?:www\.)?3839\.com/a/\d+\.htm", re.I)


def _is_timeline_game_detail_url(url: str) -> bool:
    return bool(_TIMELINE_GAME_DETAIL.search(url))


def _infer_secondary_event_type(text: str, *, game_name: str = "") -> Optional[EventType]:
    t = (text or "").replace(game_name or "", " ", 1)
    if "测试" in t or "开测" in t:
        return EventType.test
    if "正式上线" in t or "开服上线" in t or "开服" in t:
        return EventType.release
    return None


def _infer_primary_event_type(*, a_text: str, game_name: str, action_text: str) -> EventType:
    """
    优先使用条目叙述文本判断状态，避免游戏名中“上线/预下载”字样污染判定。
    """
    narrative = (a_text or "").replace(game_name or "", " ", 1)
    inferred = guess_event_type(narrative)

    if action_text == "预约":
        if "正式上线" in narrative or "开服上线" in narrative or "PC端上线" in narrative:
            return EventType.release
        if "测试" in narrative or "开测" in narrative:
            return EventType.test
        if "招募" in narrative:
            return EventType.recruit
        return EventType.reservation
    if action_text == "试玩":
        if "测试" in narrative or "开测" in narrative:
            return EventType.test
        return EventType.trial

    return inferred


def _implies_predownload_plus_secondary(a_text: str, *, game_name: str = "") -> bool:
    if not has_predownload_narrative(a_text):
        return False
    return _infer_secondary_event_type(a_text, game_name=game_name) is not None


def _predownload_secondary_dates(
    a_text: str, *, card_date: date, today: date
) -> tuple[Optional[date], Optional[date]]:
    """
    返回 (预下载日, 次级状态日)。无法从文案推断时返回 (None, None) 表示走单条解析。
    """
    dates = parse_month_days_in_text(a_text, today=today)
    if not dates:
        return (None, None)
    if len(dates) >= 2:
        return (dates[0], dates[-1])
    # 单日期：预下载挂在当前卡片日，上线/开服等为文案中的该日期
    only = dates[0]
    return (card_date, only)


def _collapse_same_url_date_type(events: List[TimelineEvent]) -> List[TimelineEvent]:
    """
    同一详情链、同一事件日、同一类型在多个日期卡片重复出现时，合并为一条并优先保留有时间的记录。
    """
    best: dict[tuple, TimelineEvent] = {}
    for e in events:
        k = (e.source_url, e.event_date, e.event_type.value, e.region.value, e.game_name)
        cur = best.get(k)
        if cur is None:
            best[k] = e
            continue
        if e.event_time and not cur.event_time:
            best[k] = e
        elif bool(e.event_time) == bool(cur.event_time) and len(e.raw_text) > len(cur.raw_text):
            best[k] = e
    return list(best.values())


def _collapse_same_url_date_conflict(events: List[TimelineEvent]) -> List[TimelineEvent]:
    """
    同一详情链接、同一天出现多个状态时，仅保留优先级最高的一条。
    """
    best: dict[tuple, TimelineEvent] = {}
    for e in events:
        k = (e.source_url, e.event_date)
        cur = best.get(k)
        if cur is None:
            best[k] = e
            continue
        cur_p = _EVENT_PRIORITY.get(cur.event_type, 0)
        new_p = _EVENT_PRIORITY.get(e.event_type, 0)
        if new_p > cur_p:
            best[k] = e
            continue
        if new_p == cur_p:
            if e.event_time and not cur.event_time:
                best[k] = e
            elif bool(e.event_time) == bool(cur.event_time) and len(e.raw_text) > len(cur.raw_text):
                best[k] = e
    return list(best.values())


def _events_from_li(
    *,
    a_text: str,
    card_date: date,
    today: date,
    game_name: str,
    region,
    source_url: str,
    action_text: str,
) -> List[TimelineEvent]:
    if _implies_predownload_plus_secondary(a_text, game_name=game_name):
        pre_d, sec_d = _predownload_secondary_dates(a_text, card_date=card_date, today=today)
        sec_type = _infer_secondary_event_type(a_text, game_name=game_name)
        if pre_d is not None and sec_d is not None and sec_type is not None:
            return [
                TimelineEvent(
                    game_name=game_name,
                    event_date=pre_d,
                    event_time=None,
                    region=region,
                    event_type=EventType.predownload,
                    tags="",
                    reservation_count=None,
                    raw_text=a_text,
                    source_url=source_url,
                ),
                TimelineEvent(
                    game_name=game_name,
                    event_date=sec_d,
                    event_time=extract_time(a_text),
                    region=region,
                    event_type=sec_type,
                    tags="",
                    reservation_count=None,
                    raw_text=a_text,
                    source_url=source_url,
                ),
            ]

    inferred = _infer_primary_event_type(a_text=a_text, game_name=game_name, action_text=action_text)
    title_dates = parse_month_days_in_text(a_text, today=today)
    if inferred in {EventType.release, EventType.test, EventType.predownload} and title_dates:
        event_date = title_dates[-1]
    else:
        event_date = card_date
    return [
        TimelineEvent(
            game_name=game_name,
            event_date=event_date,
            event_time=extract_time(a_text),
            region=region,
            event_type=inferred,
            tags="",
            reservation_count=None,
            raw_text=a_text,
            source_url=source_url,
        )
    ]


def _extract_game_name(title: str) -> str:
    """
    从时间线条目标题中提取游戏名（只保留名称本体）。
    """
    t = normalize_space(title)
    if not t:
        return ""

    t_wo_paren = re.sub(r"[\(（][^()（）]{1,20}[\)）]", "", t).strip()

    # 去掉常见后缀变体
    for sfx in _NAME_SUFFIX_CLEANUPS:
        if sfx in t_wo_paren:
            t_wo_paren = t_wo_paren.replace(sfx, "")

    # 规则：尽量在“评分/时间/事件词”出现前截断为游戏名
    name_part = t_wo_paren
    cut_points: list[int] = []

    # 时间/评分/事件词
    for m in re.finditer(r"\b\d{1,2}:\d{2}\b", name_part):
        cut_points.append(m.start())
    for m in re.finditer(r"\b\d+(\.\d+)?\b", name_part):
        # 评分一般在游戏名后面，用作截断点（跳过纯年份这类极端情况）
        if m.start() > 0:
            cut_points.append(m.start())
            break
    for kw in ["正式上线", "上线", "测试", "开测", "更新", "新版本", "新赛季", "预下载", "预约", "招募"]:
        idx = name_part.find(kw)
        if idx > 0:
            cut_points.append(idx)

    if cut_points:
        cut = min(cut_points)
        name_part = name_part[:cut].strip(" _-—")

    clean_name = normalize_space(name_part).strip(" _-—")
    # 名字里残留的多余下划线等
    clean_name = clean_name.replace("_", " ").strip()
    # 有时抓到的是“游戏名 + 品类 + 评分”，先取第一个空格前，避免出现“游戏名 三国 回合”
    if " " in clean_name:
        clean_name = clean_name.split(" ", 1)[0].strip()
    return clean_name


def parse_timeline_html(
    html: str,
    *,
    base_url: str = "https://www.3839.com/timeline.html",
    today: Optional[date] = None,
) -> List[TimelineEvent]:
    """
    将时间线 HTML 解析为 TimelineEvent 列表。

    解析原则：
    - 每条先有卡片日期 card_date；若条目标题含「M月D日」或「M.DD」(如 4.17)，则以标题内日期为准（多段时取最后一段，常为定档/正式上线)
    - 同一文案同时含「预下载」与「上线」时拆成两条：预下载日 + 正式上线日，类型分别为「预下载」「上线」
    - 只采集时间线条目中的游戏详情链（3839.com/a/*.htm），不请求详情页、不扩展抓取其它游戏
    - 只采集“条目标题链接”，跳过“下载/预约/试玩”等动作链接
    """
    soup = BeautifulSoup(html, "lxml")
    # 仅解析主时间线区块（全部），避免跨“抢先爆料/其他分组”串日期
    scope = soup.select_one("div.panelList div.foreArea") or soup

    events: List[TimelineEvent] = []
    # 按“日期卡片”解析，避免全局游标串位
    cards = scope.select("div.foreCard")
    for card in cards:
        header = card.select_one(".foreCard-hd") or card.select_one(".foreDate")
        header_text = normalize_space(header.get_text(" ", strip=True)) if header else ""
        card_date = parse_month_day_to_date(header_text, today=today)
        if card_date is None:
            # fallback：从卡片整体文本前部尝试日期
            card_text = normalize_space(card.get_text(" ", strip=True))
            card_date = parse_month_day_to_date(card_text[:30], today=today)
        if card_date is None:
            continue

        for li in card.select("ul.foreList > li"):
            # 每个条目只取第一个链接（标题），忽略“下载/预约/试玩”
            first_link = None
            action_text = ""
            for a in li.select("a[href]"):
                text = normalize_space(a.get_text(" ", strip=True))
                if text in _ACTION_TEXTS:
                    action_text = text
                    break
            for a in li.select("a[href]"):
                text = normalize_space(a.get_text(" ", strip=True))
                if text and text not in _ACTION_TEXTS:
                    first_link = a
                    break
            if first_link is None:
                continue

            href = first_link.get("href")
            if not href:
                continue
            a_text = normalize_space(first_link.get_text(" ", strip=True))
            if not a_text:
                continue

            source_url = urljoin(base_url, href)
            if not _is_timeline_game_detail_url(source_url):
                continue

            game_name = _extract_game_name(a_text)
            if not game_name:
                continue

            region = guess_region(a_text)
            tday = today or date.today()
            for ev in _events_from_li(
                a_text=a_text,
                card_date=card_date,
                today=tday,
                game_name=game_name,
                region=region,
                source_url=source_url,
                action_text=action_text,
            ):
                events.append(ev)

    events = _collapse_same_url_date_type(events)
    events = _collapse_same_url_date_conflict(events)

    # 同一条目在页面里可能出现两次（例如不同板块），先做一次简单去重
    uniq: dict[tuple, TimelineEvent] = {}
    for e in events:
        k = (
            e.game_name,
            e.event_date,
            e.event_time,
            e.event_type.value,
            e.region.value,
            e.source_url,
        )
        uniq.setdefault(k, e)

    return list(uniq.values())

