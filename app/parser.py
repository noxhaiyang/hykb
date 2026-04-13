from __future__ import annotations

import re
from datetime import date
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import TimelineEvent
from .utils import (
    extract_time,
    guess_event_type,
    guess_region,
    normalize_space,
    parse_month_day_to_date,
)


_ACTION_TEXTS = {"下载", "预约", "试玩", "关注"}

_NAME_SUFFIX_CLEANUPS = [
    "-新版本预约",
    "-新版本",
    "-体验服",
    "-近期爆火新游",
    "-全角色&武器免费",
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
    - 以页面顺序维护当前日期（遇到 '04月07日 今天' 之类行就更新）
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
            game_name = _extract_game_name(a_text)
            event_time = extract_time(a_text)
            region = guess_region(a_text)
            event_type = guess_event_type(a_text)

            if not game_name:
                continue

            events.append(
                TimelineEvent(
                    game_name=game_name,
                    event_date=card_date,
                    event_time=event_time,
                    region=region,
                    event_type=event_type,
                    tags="",
                    reservation_count=None,
                    raw_text=a_text,
                    source_url=source_url,
                )
            )

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

    deduped = list(uniq.values())

    # 再按 source_url + 事件类型 做二次归并：同链接在多个日期出现时仅保留最早日期。
    # 这是为了解决 timeline 页面多个区块重复挂载同一条目的问题。
    by_source: dict[tuple[str, str], TimelineEvent] = {}
    for e in deduped:
        k2 = (e.source_url, e.event_type.value)
        old = by_source.get(k2)
        if old is None:
            by_source[k2] = e
            continue
        old_when = (old.event_date, old.event_time or "99:99")
        new_when = (e.event_date, e.event_time or "99:99")
        if new_when < old_when:
            by_source[k2] = e

    return list(by_source.values())

