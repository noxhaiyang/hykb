from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from app.analytics import build_dashboard
from app.store import connect, init_db, query_events
from app.utils import clamp_date_range


def _to_date_str(d: date | None) -> str | None:
    return d.isoformat() if d else None


def main() -> None:
    st.set_page_config(page_title="好游快爆游戏日历", layout="wide")
    st.title("好游快爆游戏日历")

    today = date.today()
    default_end = today + timedelta(days=30)
    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
    with col1:
        start = st.date_input("开始日期", value=today)
    with col2:
        end = st.date_input("结束日期", value=default_end)
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

    dashboard = build_dashboard(rows, today=today)
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

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("总事件数", dashboard["total"])
    k2.metric("海外占比", f"{dashboard['overseas_rate']}%")
    k3.metric("上线事件", dashboard["release_cnt"])
    k4.metric("缺失时间率", f"{dashboard['missing_time_rate']}%")
    st.caption(
        "测试 {test_cnt} | 预下载 {predownload_cnt} | 国内 {domestic} | 海外 {overseas}".format(
            test_cnt=dashboard["test_cnt"],
            predownload_cnt=dashboard["predownload_cnt"],
            domestic=dashboard["domestic"],
            overseas=dashboard["overseas"],
        )
    )

    st.caption(f"共 {len(data)} 条")
    st.dataframe(data, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
