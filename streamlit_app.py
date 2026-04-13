from __future__ import annotations

from datetime import date

import streamlit as st

from app.store import connect, init_db, query_events
from app.utils import clamp_date_range


def _to_date_str(d: date | None) -> str | None:
    return d.isoformat() if d else None


def main() -> None:
    st.set_page_config(page_title="好游快爆游戏日历", layout="wide")
    st.title("好游快爆游戏日历")

    today = date.today()
    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
    with col1:
        start = st.date_input("开始日期", value=today)
    with col2:
        end = st.date_input("结束日期", value=today)
    with col3:
        region_label = st.selectbox("地区", ["全部", "国内", "海外"], index=0)
    with col4:
        keyword = st.text_input("关键字", value="", placeholder="游戏名或文本关键字")

    start, end = clamp_date_range(start, end)
    region = {"全部": "all", "国内": "domestic", "海外": "overseas"}[region_label]

    conn = connect()
    try:
        init_db(conn)
        rows = query_events(
            conn,
            start=_to_date_str(start),
            end=_to_date_str(end),
            region=region,
            q=keyword.strip() or None,
            limit=2000,
        )
    finally:
        conn.close()

    data = []
    for r in rows:
        data.append(
            {
                "游戏名称": r["game_name"],
                "上线日期": r["event_date"],
                "上线时间": r["event_time"] or "",
                "地区": "海外" if r["region"] == "overseas" else "国内",
                "事件类型": r["event_type"],
                "来源URL": r["source_url"],
                "更新时间": r["updated_at"],
            }
        )

    st.caption(f"共 {len(data)} 条")
    st.dataframe(data, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
