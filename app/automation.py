from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .analytics import build_dashboard
from .crawler import run_once
from .report_dingtalk import run_report
from .store import connect, init_db, query_events


def _artifact_paths() -> tuple[Path, Path]:
    base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "competition_snapshot.json", base / "competition_metrics.jsonl"


def run_pipeline(
    *,
    days: int = 30,
    send_dingtalk: bool = False,
    webhook: Optional[str] = None,
    secret: Optional[str] = None,
    style: str = "brief",
    manual_minutes: float = 20.0,
) -> dict:
    started = time.perf_counter()
    crawl_stats = run_once()

    start = date.today()
    end = start + timedelta(days=days)
    conn = connect()
    try:
        init_db(conn)
        rows = query_events(
            conn,
            start=start.isoformat(),
            end=end.isoformat(),
            region="all",
            q=None,
            limit=3000,
        )
    finally:
        conn.close()

    dashboard = build_dashboard(rows, today=start)
    elapsed_seconds = round(time.perf_counter() - started, 2)
    auto_minutes = round(elapsed_seconds / 60.0, 2)
    saved_minutes = round(max(0.0, manual_minutes - auto_minutes), 2)
    saved_ratio = round((saved_minutes / manual_minutes) * 100.0, 1) if manual_minutes > 0 else 0.0

    dingtalk_status = "skipped"
    if send_dingtalk:
        run_report(
            webhook=webhook,
            secret=secret,
            days=days,
            dry_run=False,
            style=style,
        )
        dingtalk_status = "sent"

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": days,
        "crawler": crawl_stats,
        "dashboard": dashboard,
        "automation": {
            "runtime_seconds": elapsed_seconds,
            "auto_minutes": auto_minutes,
            "manual_minutes": manual_minutes,
            "saved_minutes": saved_minutes,
            "saved_ratio": saved_ratio,
        },
        "dingtalk": dingtalk_status,
    }

    snapshot_path, history_path = _artifact_paths()
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return {
        "payload": payload,
        "snapshot_path": str(snapshot_path),
        "history_path": str(history_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.automation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="执行抓取+统计+可选推送的一体化流水线")
    run_p.add_argument("--days", default=30, type=int, help="统计未来天数，默认30")
    run_p.add_argument("--send-dingtalk", action="store_true", help="是否发送钉钉日报")
    run_p.add_argument("--webhook", default=None, help="钉钉机器人 webhook")
    run_p.add_argument("--secret", default=None, help="钉钉加签 secret（如开启签名）")
    run_p.add_argument(
        "--style",
        default="brief",
        choices=["brief", "full"],
        help="日报样式：brief(老板简版) / full(详细版)",
    )
    run_p.add_argument(
        "--manual-minutes",
        default=20.0,
        type=float,
        help="人工执行同流程的耗时（分钟），用于量化自动化节省，默认20",
    )

    args = parser.parse_args()
    if args.cmd == "run":
        result = run_pipeline(
            days=args.days,
            send_dingtalk=args.send_dingtalk,
            webhook=args.webhook,
            secret=args.secret,
            style=args.style,
            manual_minutes=args.manual_minutes,
        )
        print("pipeline done.")
        print(f"snapshot={result['snapshot_path']}")
        print(f"history={result['history_path']}")
        auto = result["payload"]["automation"]
        print(
            "efficiency: "
            f"runtime={auto['runtime_seconds']}s "
            f"saved={auto['saved_minutes']}min "
            f"saved_ratio={auto['saved_ratio']}%"
        )


if __name__ == "__main__":
    main()
