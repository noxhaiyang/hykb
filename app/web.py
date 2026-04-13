from __future__ import annotations

import argparse
import csv
import io
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .store import connect, init_db, query_events
from .utils import clamp_date_range, parse_date_range


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

        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "rows": rows,
                "filters": {"start": start_s or "", "end": end_s or "", "region": region, "q": q or ""},
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

