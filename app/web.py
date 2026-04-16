from __future__ import annotations

import argparse
import csv
import io
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analytics import build_dashboard
from .crawler import fetch_timeline_html
from .parser import parse_timeline_html
from .store import connect, init_db, query_events
from .utils import clamp_date_range, normalize_space, parse_date_range, parse_month_day_to_date


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="好游快爆游戏日历", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        region: str = Query(default="all"),
        q: Optional[str] = Query(default=None),
    ):
        if not start:
            start = date.today().isoformat()
        s, e = parse_date_range(start, end)
        s, e = clamp_date_range(s, e)
        start_s = s.isoformat() if s else None
        end_s = e.isoformat() if e else None

        conn = connect()
        try:
            init_db(conn)
            rows = query_events(conn, start=start_s, end=end_s, region=region, q=q, limit=800)
        finally:
            conn.close()
        dashboard = build_dashboard(rows)
        total = max(1, dashboard["total"])
        event_counter = Counter(r["event_type"] for r in rows)
        event_stats = [
            {"name": k, "count": v, "pct": round(v * 100.0 / total, 1)}
            for k, v in sorted(event_counter.items(), key=lambda x: (-x[1], x[0]))
        ]
        region_stats = [
            {"name": "国内", "count": dashboard["domestic"], "pct": round(dashboard["domestic"] * 100.0 / total, 1)},
            {"name": "海外", "count": dashboard["overseas"], "pct": round(dashboard["overseas"] * 100.0 / total, 1)},
        ]
        daily_counter = Counter(r["event_date"] for r in rows)
        trend_dates = sorted(daily_counter.keys())
        trend_data = [{"date": d, "count": daily_counter[d]} for d in trend_dates]
        peak_day = max(trend_data, key=lambda x: x["count"]) if trend_data else {"date": "-", "count": 0}
        top_event = event_stats[0] if event_stats else {"name": "-", "count": 0, "pct": 0.0}
        viz_insights = [
            f"事件类型以「{top_event['name']}」为主，数量 {top_event['count']}，占比 {top_event['pct']}%",
            f"峰值日期为 {peak_day['date']}，当天事件数 {peak_day['count']}",
            f"未来7天共有 {dashboard['next7']} 条事件，较后7天差值 {dashboard['next7_delta']}",
        ]

        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "rows": rows,
                "filters": {"start": start_s or "", "end": end_s or "", "region": region, "q": q or ""},
                "dashboard": dashboard,
                "event_stats": event_stats,
                "region_stats": region_stats,
                "trend_data": trend_data,
                "viz_insights": viz_insights,
            },
        )

    @app.get("/export.csv")
    def export_csv(
        start: Optional[str] = Query(default=None),
        end: Optional[str] = Query(default=None),
        region: str = Query(default="all"),
        q: Optional[str] = Query(default=None),
    ):
        if not start:
            start = date.today().isoformat()
        s, e = parse_date_range(start, end)
        s, e = clamp_date_range(s, e)
        start_s = s.isoformat() if s else None
        end_s = e.isoformat() if e else None

        conn = connect()
        try:
            init_db(conn)
            rows = query_events(conn, start=start_s, end=end_s, region=region, q=q, limit=5000)
        finally:
            conn.close()

        def stream():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(
                [
                    "游戏名称",
                    "上线日期",
                    "上线时间",
                    "地区",
                    "事件类型",
                    "来源URL",
                    "原始文本",
                    "更新时间",
                ]
            )
            for r in rows:
                region_cn = "海外" if r["region"] == "overseas" else "国内"
                w.writerow(
                    [
                        r["game_name"],
                        r["event_date"],
                        r["event_time"] or "",
                        region_cn,
                        r["event_type"],
                        r["source_url"],
                        r["raw_text"],
                        r["updated_at"],
                    ]
                )
            yield buf.getvalue().encode("utf-8-sig")

        filename = "hykb_game_calendar.csv"
        return StreamingResponse(
            stream(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/reconcile")
    def reconcile(day: Optional[str] = Query(default=None)):
        """
        对账指定日期：页面条目数 / 解析条目数 / 入库条目数。
        示例：/reconcile?day=2026-04-16
        """
        target_day = date.fromisoformat(day) if day else date.today()
        day_s = target_day.isoformat()

        html = fetch_timeline_html()
        parsed = parse_timeline_html(html)
        parsed_day = [e for e in parsed if e.event_date.isoformat() == day_s]

        # 页面原始计数：按日期卡片中的 li 数量统计
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        scope = soup.select_one("div.panelList div.foreArea") or soup
        page_day_count = 0
        page_day_samples: list[str] = []
        for card in scope.select("div.foreCard"):
            header = card.select_one(".foreCard-hd") or card.select_one(".foreDate")
            header_text = normalize_space(header.get_text(" ", strip=True)) if header else ""
            card_date = parse_month_day_to_date(header_text, today=target_day)
            if card_date and card_date.isoformat() == day_s:
                items = card.select("ul.foreList > li")
                page_day_count += len(items)
                if not page_day_samples:
                    page_day_samples = [normalize_space(li.get_text(" ", strip=True))[:80] for li in items[:5]]

        conn = connect()
        try:
            init_db(conn)
            db_rows = query_events(conn, start=day_s, end=day_s, region="all", q=None, limit=5000)
        finally:
            conn.close()

        parsed_keys = {
            (e.game_name, e.event_date.isoformat(), e.event_type.value, e.event_time or "")
            for e in parsed_day
        }
        db_keys = {
            (r["game_name"], r["event_date"], r["event_type"], r["event_time"] or "")
            for r in db_rows
        }
        only_in_parsed = sorted(parsed_keys - db_keys)[:10]
        only_in_db = sorted(db_keys - parsed_keys)[:10]

        return JSONResponse(
            {
                "date": day_s,
                "counts": {
                    "page_items": page_day_count,
                    "parsed_items": len(parsed_day),
                    "db_items": len(db_rows),
                },
                "is_aligned": page_day_count == len(parsed_day) == len(db_rows),
                "samples": {
                    "page_first_5": page_day_samples,
                    "only_in_parsed_top10": only_in_parsed,
                    "only_in_db_top10": only_in_db,
                },
            }
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.web")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="启动本地Web UI")
    run_p.add_argument("--host", default="127.0.0.1")
    run_p.add_argument("--port", default=8000, type=int)

    args = parser.parse_args()
    if args.cmd == "run":
        import uvicorn

        uvicorn.run("app.web:create_app", host=args.host, port=args.port, factory=True)


if __name__ == "__main__":
    main()

