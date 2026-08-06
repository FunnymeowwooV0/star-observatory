#!/usr/bin/env python3
"""薄 CLI:抓 Reddit r/LocalLLaMA 本週熱帖,印 Markdown 表到 stdout。

給每週導讀排程任務(本機、住宅 IP)呼叫,見 goals/05-reddit週熱帖.md。
**不進 GitHub Actions**(資料中心 IP 可能被 Reddit 擋)。

抓取/解析失敗 → stderr 印錯誤、exit 1(不印空表假裝成功)。
用法:
    python3 scripts/reddit_weekly.py            # 只印 Markdown
    python3 scripts/reddit_weekly.py --save     # 額外 append 進 data/reddit_weekly.csv(同日冪等)

--save 的資料供 fetch_trends.py 的 run_daily() 讀最新一次快照渲染網頁 Reddit tab,
見 goals/18-reddit上頁面.md。
"""
import argparse
import datetime as dt
import sys

from sources_extra import fetch_reddit_top
from fetch_trends import (TZ, append_csv, build_reddit_save_rows, read_csv_rows,
                          REDDIT_CSV_HEADER, REDDIT_CSV_PATH)

# CSV 正本(路徑/欄位/冪等規則)在 fetch_trends.py,雲端管線與本 CLI 共用同一份,不各寫一套。
CSV_PATH = REDDIT_CSV_PATH
CSV_HEADER = REDDIT_CSV_HEADER
build_save_rows = build_reddit_save_rows


def render_markdown(items):
    lines = ["| # | 標題 | ▲ 分數 | 💬 留言 |", "|---|---|---|---|"]
    for i, it in enumerate(items, start=1):
        score = it["score"] if it["score"] is not None else "-"
        comments = it["comments"] if it["comments"] is not None else "-"
        title_link = f"[{it['title']}]({it['permalink']})"
        if it["external_url"]:
            title_link += f" [[原文]]({it['external_url']})"
        lines.append(f"| {i} | {title_link} | {score} | {comments} |")
    return "\n".join(lines)


def save_snapshot(items, date=None):
    """讀既有 CSV → 算冪等後要寫入的列(見 build_save_rows)→ 真的寫入。回傳寫入列數。"""
    if date is None:
        date = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    existing = read_csv_rows(CSV_PATH)
    rows = build_save_rows(items, date, existing)
    if rows:
        append_csv(CSV_PATH, CSV_HEADER, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true",
                     help="抓成功後 append 進 data/reddit_weekly.csv(同日已有快照則跳過)")
    args = ap.parse_args()

    try:
        items = fetch_reddit_top()
    except Exception as e:  # noqa: BLE001 - 薄 CLI,任何失敗都要如實回報並 exit 1
        print(f"抓取 Reddit r/LocalLLaMA 週榜失敗:{e}", file=sys.stderr)
        return 1
    print(render_markdown(items))

    if args.save:
        n = save_snapshot(items)
        if n:
            print(f"[reddit_weekly] 已寫入 data/reddit_weekly.csv:{n} 列", file=sys.stderr)
        else:
            print("[reddit_weekly] 今日已有快照,跳過寫入(冪等)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
