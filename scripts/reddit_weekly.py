#!/usr/bin/env python3
"""薄 CLI:抓 Reddit r/LocalLLaMA 本週熱帖,印 Markdown 表到 stdout。

給每週導讀排程任務(本機、住宅 IP)呼叫,見 goals/05-reddit週熱帖.md。
**不進 GitHub Actions**(資料中心 IP 可能被 Reddit 擋)。

抓取/解析失敗 → stderr 印錯誤、exit 1(不印空表假裝成功)。
用法:python3 scripts/reddit_weekly.py
"""
import sys

from sources_extra import fetch_reddit_top


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


def main():
    try:
        items = fetch_reddit_top()
    except Exception as e:  # noqa: BLE001 - 薄 CLI,任何失敗都要如實回報並 exit 1
        print(f"抓取 Reddit r/LocalLLaMA 週榜失敗:{e}", file=sys.stderr)
        return 1
    print(render_markdown(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
