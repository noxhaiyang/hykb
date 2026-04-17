from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import date, timedelta
from typing import Optional

import requests

from .store import connect, init_db, query_events


def _build_signed_webhook(webhook: str, secret: Optional[str]) -> str:
    if not secret:
        return webhook
    ts = str(int(time.time() * 1000))
    sign_str = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), sign_str, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def _build_report_markdown(rows: list[dict], *, start: date, end: date) -> str:
    overseas = [r for r in rows if r["region"] == "overseas"]
    domestic = [r for r in rows if r["region"] != "overseas"]

    lines: list[str] = []
    lines.append(f"### 游戏日历日报（{start.isoformat()} ~ {end.isoformat()}）")
    lines.append(f"- 共 {len(rows)} 款，海外 {len(overseas)} 款，国内 {len(domestic)} 款")
    lines.append("")
    lines.append("#### 海外重点")
    if not overseas:
        lines.append("- 近一个月暂无海外游戏")
    else:
        for r in overseas[:25]:
            when = f"{r['event_date']}" + (f" {r['event_time']}" if r["event_time"] else "")
            lines.append(
                f"- **{r['game_name']}** | {when} | {r['event_type']} | [详情]({r['source_url']})"
            )
    lines.append("")
    lines.append("#### 近一个月全部（前50条）")
    for r in rows[:50]:
        when = f"{r['event_date']}" + (f" {r['event_time']}" if r["event_time"] else "")
        region_cn = "海外" if r["region"] == "overseas" else "国内"
        prefix = "【海外】" if r["region"] == "overseas" else "【国内】"
        lines.append(
            f"- {prefix}{r['game_name']} | {when} | {region_cn} | {r['event_type']} | [详情]({r['source_url']})"
        )
    return "\n".join(lines)


def _build_report_markdown_brief(rows: list[dict], *, start: date, end: date) -> str:
    """
    老板视图：保留海外TOP10 + 未来7天看点，排版整齐易扫读。
    """
    overseas = [r for r in rows if r["region"] == "overseas"]
    domestic = [r for r in rows if r["region"] != "overseas"]

    def sort_key(r: dict):
        return (r["event_date"], r["event_time"] or "", r["game_name"])

    all_sorted = sorted(rows, key=sort_key)
    overseas_sorted = sorted(overseas, key=sort_key)
    next7_end = (start + timedelta(days=7)).isoformat()
    next7 = [r for r in all_sorted if r["event_date"] <= next7_end]

    def fmt_when(r: dict) -> str:
        return f"{r['event_date']}" + (f" {r['event_time']}" if r["event_time"] else "")

    def fmt_line(idx: int, r: dict) -> str:
        region_cn = "海外" if r["region"] == "overseas" else "国内"
        return (
            f"{idx:02d}. **{r['game_name']}**\n"
            f"    - 时间：{fmt_when(r)} ｜ 地区：{region_cn} ｜ 状态：{r['event_type']}"
        )

    lines: list[str] = []
    lines.append(f"### 游戏日历月报简版（{start.isoformat()} ~ {end.isoformat()}）")
    lines.append("")
    lines.append("#### 核心概览")
    lines.append(f"- 总量：**{len(rows)}** | 海外：**{len(overseas)}** | 国内：**{len(domestic)}**")
    lines.append(f"- 未来7天事件 **{len(next7)}** 条")
    lines.append(f"- 海外重点窗口（未来7天）**{sum(1 for r in next7 if r['region'] == 'overseas')}** 条")
    lines.append("")
    lines.append("#### 海外TOP10（重点）")
    if not overseas_sorted:
        lines.append("- 未来窗口内暂无海外重点游戏")
    else:
        for i, r in enumerate(overseas_sorted[:10], start=1):
            lines.append(fmt_line(i, r))
    lines.append("")
    lines.append("#### 未来7天看点（前30条）")
    if not next7:
        lines.append("- 未来7天暂无新增看点")
    else:
        for i, r in enumerate(next7[:30], start=1):
            lines.append(fmt_line(i, r))
    return "\n".join(lines)


def run_report(
    *,
    webhook: Optional[str],
    secret: Optional[str],
    days: int = 30,
    dry_run: bool = False,
    style: str = "brief",
) -> str:
    start = date.today()
    end = start + timedelta(days=days)

    conn = connect()
    try:
        init_db(conn)
        db_rows = query_events(
            conn,
            start=start.isoformat(),
            end=end.isoformat(),
            region="all",
            q=None,
            limit=2000,
        )
    finally:
        conn.close()

    rows = [dict(r) for r in db_rows]
    if style == "full":
        markdown_text = _build_report_markdown(rows, start=start, end=end)
    else:
        markdown_text = _build_report_markdown_brief(rows, start=start, end=end)

    if dry_run:
        return markdown_text

    if not webhook:
        raise ValueError("webhook 不能为空（可用 --dry-run 仅预览内容）")

    signed_url = _build_signed_webhook(webhook, secret)
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"游戏日历日报 {start.isoformat()}",
            "text": markdown_text,
        },
    }
    resp = requests.post(signed_url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.text


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.report_dingtalk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="发送钉钉群游戏日报")
    run_p.add_argument("--webhook", default=None, help="钉钉机器人 webhook")
    run_p.add_argument("--secret", default=None, help="钉钉加签 secret（如开启签名）")
    run_p.add_argument("--days", default=30, type=int, help="统计未来天数，默认30")
    run_p.add_argument("--dry-run", action="store_true", help="仅打印内容，不发送")
    run_p.add_argument(
        "--style",
        default="brief",
        choices=["brief", "full"],
        help="日报样式：brief(老板简版) / full(详细版)",
    )

    args = parser.parse_args()
    if args.cmd == "run":
        result = run_report(
            webhook=args.webhook,
            secret=args.secret,
            days=args.days,
            dry_run=args.dry_run,
            style=args.style,
        )
        if args.dry_run:
            print(result)
        else:
            print(f"dingtalk send done: {result}")


if __name__ == "__main__":
    main()

