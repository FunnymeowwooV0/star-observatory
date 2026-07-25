#!/usr/bin/env python3
"""
一次性回補腳本 — 補 2026-07-16 ～ 2026-07-21(前六天)歷史資料進 data/*.csv。

用法:
    python3 scripts/backfill_history.py            # 冪等:已有該天資料就跳過
    python3 scripts/backfill_history.py --force     # 強制覆寫(在該天既有資料後再 append 一輪)

範圍與作法(對照 goals/15-回補前六天.md):
    - GitHub(Wayback Machine):CDX 查 github.com/trending 快照,抓 `id_` 後綴的原始 HTML,
      用既有 fetch_trends.parse_github_trending() 解析,取前 10 append 進 data/github_daily.csv。
    - HN(官方 Algolia):每天用 created_at_i 區間(台北時區當日 00:00~翌日 00:00)查 story,
      用既有 sources_extra.parse_hn_frontpage() 在**客戶端按 points 降冪**取前 10。
      **口徑差異聲明**:這是「當日發布的帖,以現在(回補當下)的分數」,
      跟平常 daily 抓的「當時的 HN 頭版(frontpage tag,即時排序)」不是同一件事 ——
      本回補資料屬於「事後分數快照」,不是「當時頭版真實樣貌」,讀者比較歷史數字時要留意。
    - OpenRouter:rankings API 一次回傳的資料本就含 2026-07-17～07-21 五天,對每個尚未在
      CSV 的日期用 parse_openrouter_rankings(..., target_date=date) 取該天,按 model 聚合
      (與平常抓法同一套聚合邏輯,只是換一天)取前 10 append。07-16 該 API 沒有資料,跳過。
    - Ollama(選配):CDX 查 ollama.com/library 快照,有的天數用既有 parse_ollama_library()
      解析 append(pulls_delta 留空,由讀取端 compute_pull_deltas 算);沒快照的天數印出跳過。
    - Product Hunt / Hugging Face:本工單明確不做(PH 需雲端 token;HF 無歷史查詢管道),
      腳本印出跳過原因,不硬湊。

硬規則:
    - 冪等:append 前檢查該 (date, CSV) 是否已有資料列,有 → 跳過(--force 例外)。
    - **絕不觸碰 2026-07-22(今天)的既有列** —— BACKFILL_DATES 天生不含今天,
      且本腳本只用 append(csv writer 只增不刪不改),不會動到既有列。
    - 不改 CSV schema、不動 fetch_trends.py / history.py 既有邏輯
      (openrouter 解析函式加的 target_date 參數,預設 None,行為與原本完全相同)。
    - 對 Wayback 的請求間 sleep ≥1s,失敗重試最多 1 次。
"""
import argparse
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_trends as ft   # noqa: E402  (重用既有純解析函式與 CSV I/O,不重寫邏輯)
import sources_extra as se  # noqa: E402

UA = ft.UA
TZ = ft.TZ
TODAY = "2026-07-22"
BACKFILL_DATES = ["2026-07-16", "2026-07-17", "2026-07-18",
                   "2026-07-19", "2026-07-20", "2026-07-21"]
assert TODAY not in BACKFILL_DATES, "絕不回補今天"

SLEEP_SECS = 1.2  # ≥1s,對 Wayback 禮貌


def _sleep():
    time.sleep(SLEEP_SECS)


def _existing_dates(csv_path):
    return {r.get("date") for r in ft.read_csv_rows(csv_path)}


def _fetch_with_retry(url, as_json=False, retries=1):
    """GET,失敗重試最多 1 次(共嘗試 retries+1 次)。回 None 代表放棄。"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
            return resp.json() if as_json else resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(SLEEP_SECS)
    print(f"    (請求失敗,已重試:{last_err})")
    return None


def _cdx_snapshots(url_pattern, frm, to):
    """查 Wayback CDX,回 {date 'YYYY-MM-DD': timestamp} dict(collapse=timestamp:8,約一天一筆)。"""
    cdx_url = (f"https://web.archive.org/cdx/search/cdx?url={url_pattern}"
               f"&from={frm}&to={to}&output=json&filter=statuscode:200&collapse=timestamp:8")
    data = _fetch_with_retry(cdx_url, as_json=True)
    if not data or len(data) < 2:
        return {}
    out = {}
    for row in data[1:]:
        ts = row[1]
        date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        out[date] = ts
    return out


# ----------------------------- 各源回補 -----------------------------
def backfill_github(force=False):
    print("== GitHub(Wayback Machine)==")
    existing = _existing_dates("data/github_daily.csv")
    snap_map = _cdx_snapshots("github.com/trending", "20260716", "20260721")
    for date in BACKFILL_DATES:
        if date in existing and not force:
            print(f"  {date}: 已存在,跳過")
            continue
        ts = snap_map.get(date)
        if not ts:
            print(f"  {date}: 無 Wayback 快照,跳過")
            continue
        url = f"https://web.archive.org/web/{ts}id_/https://github.com/trending"
        html = _fetch_with_retry(url)
        _sleep()
        if html is None:
            print(f"  {date}: 抓取失敗,跳過")
            continue
        try:
            repos = ft.parse_github_trending(html, top=ft.GH_TOP)
        except Exception as e:  # noqa: BLE001
            print(f"  {date}: 解析失敗({e}),跳過")
            continue
        rows = [[date, i + 1, r["name"], r["period_stars"], r["total_stars"], r["lang"], r["url"], r["desc"]]
                for i, r in enumerate(repos)]
        ft.append_csv("data/github_daily.csv",
                       ["date", "rank", "repo", "new_stars", "total_stars", "language", "url", "description"],
                       rows)
        print(f"  {date}: 成功,{len(rows)} 筆(快照 {ts})")


def backfill_hn(force=False):
    print("== HN(官方 Algolia,口徑=事後分數快照,見 docstring)==")
    existing = _existing_dates("data/hn_daily.csv")
    for date in BACKFILL_DATES:
        if date in existing and not force:
            print(f"  {date}: 已存在,跳過")
            continue
        y, m, d = (int(x) for x in date.split("-"))
        start = dt.datetime(y, m, d, tzinfo=TZ)
        end = start + dt.timedelta(days=1)
        s_epoch, e_epoch = int(start.timestamp()), int(end.timestamp())
        url = (f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters="
               f"created_at_i>={s_epoch},created_at_i<{e_epoch}&hitsPerPage=50")
        data = _fetch_with_retry(url, as_json=True)
        _sleep()
        if data is None:
            print(f"  {date}: 抓取失敗,跳過")
            continue
        items = se.parse_hn_frontpage(data, top=se.HN_TOP)
        if not items:
            print(f"  {date}: 當日無 story,跳過")
            continue
        rows = [[date, i + 1, it["title"], it["points"], it["comments"], it["hn_url"], it["url"]]
                for i, it in enumerate(items)]
        ft.append_csv("data/hn_daily.csv",
                       ["date", "rank", "title", "points", "comments", "hn_url", "url"], rows)
        print(f"  {date}: 成功,{len(rows)} 筆")


def backfill_openrouter(force=False):
    print("== OpenRouter(rankings API,一次回傳已含近幾天)==")
    existing = _existing_dates("data/openrouter_daily.csv")
    data = _fetch_with_retry("https://openrouter.ai/api/frontend/v1/rankings/models", as_json=True)
    _sleep()
    if data is None:
        print("  抓取失敗,全部跳過")
        return
    for date in BACKFILL_DATES:
        if date in existing and not force:
            print(f"  {date}: 已存在,跳過")
            continue
        try:
            items = se.parse_openrouter_rankings(data, top=se.OR_TOP, target_date=date)
        except RuntimeError as e:
            print(f"  {date}: API 資料裡沒有這天({e}),跳過")
            continue
        rows = [[date, i + 1, it["model"], it["total_tokens"], it["prompt_tokens"], it["completion_tokens"]]
                for i, it in enumerate(items)]
        ft.append_csv("data/openrouter_daily.csv",
                       ["date", "rank", "model", "total_tokens", "prompt_tokens", "completion_tokens"], rows)
        print(f"  {date}: 成功,{len(rows)} 筆")


def backfill_ollama(force=False):
    print("== Ollama(Wayback,選配)==")
    existing = _existing_dates("data/ollama_daily.csv")
    snap_map = _cdx_snapshots("ollama.com/library", "20260716", "20260721")
    for date in BACKFILL_DATES:
        if date in existing and not force:
            print(f"  {date}: 已存在,跳過")
            continue
        ts = snap_map.get(date)
        if not ts:
            print(f"  {date}: 無 Wayback 快照,跳過")
            continue
        url = f"https://web.archive.org/web/{ts}id_/https://ollama.com/library"
        html = _fetch_with_retry(url)
        _sleep()
        if html is None:
            print(f"  {date}: 抓取失敗,跳過")
            continue
        try:
            items = se.parse_ollama_library(html, top=se.OL_TOP)
        except Exception as e:  # noqa: BLE001
            print(f"  {date}: 解析失敗({e}),跳過")
            continue
        rows = [[date, i + 1, it["name"], it["pulls"], "", ";".join(it["caps"]), it["updated"], it["url"], it["desc"]]
                for i, it in enumerate(items)]
        ft.append_csv("data/ollama_daily.csv",
                       ["date", "rank", "model", "pulls", "pulls_delta", "caps", "updated", "url", "desc"], rows)
        print(f"  {date}: 成功,{len(rows)} 筆(快照 {ts};pulls_delta 留空,由讀取端 compute_pull_deltas 算)")


def ph_window(date_str):
    """PH 歷史回補的 24h 窗(純函式,可離線測)。

    對齊平常每日抓榜口徑:每天 01:00 UTC(=09:00 台北)收單、回看 24h。
    date_str='YYYY-MM-DD'(台北榜日)→ 回 (posted_after, posted_before) ISO8601 字串,
    即 前一日 01:00 UTC → 當日 01:00 UTC。
    """
    y, m, d = (int(x) for x in date_str.split("-"))
    end = dt.datetime(y, m, d, 1, 0, 0, tzinfo=dt.timezone.utc)
    start = end - dt.timedelta(hours=24)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def backfill_ph(force=False):
    """PH 歷史回補(2026-07-23 主理人拍板「補」)。只能在有 PH_TOKEN 的環境跑(GitHub Actions)。

    口徑聲明:votesCount=查詢當下票數(事後快照),非當日即時票數;與 HN 回補同類限制。
    """
    print("== Product Hunt(官方 GraphQL,需 PH_TOKEN;口徑=事後票數快照)==")
    token = os.environ.get("PH_TOKEN")
    if not token:
        print("  ⛔ 環境變數 PH_TOKEN 不存在——本機無法回補 PH,請在 GitHub Actions 跑(backfill-ph workflow)")
        sys.exit(1)
    existing = _existing_dates("data/ph_daily.csv")
    for date in BACKFILL_DATES:
        if date in existing and not force:
            print(f"  {date}: 已存在,跳過")
            continue
        after, before = ph_window(date)
        try:
            items = se.fetch_ph_posts(token, posted_after=after, posted_before=before)
        except Exception as e:  # noqa: BLE001
            print(f"  {date}: 抓取失敗({e}),跳過")
            _sleep()
            continue
        _sleep()
        if not items:
            print(f"  {date}: 該窗無貼文,跳過")
            continue
        rows = [[date, i + 1, p["name"], p["tagline"], p["votes"], p["url"]]
                for i, p in enumerate(items)]
        ft.append_csv("data/ph_daily.csv",
                       ["date", "rank", "name", "tagline", "votes", "url"], rows)
        print(f"  {date}: 成功,{len(rows)} 筆")


def skip_ph_hf():
    print("== Product Hunt:預設跑不做(需雲端 token;要補用 --ph-only 在 Actions 跑)==")
    print("== Hugging Face:不做(官方無歷史查詢管道)——跳過 ==")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="忽略既有資料,強制再 append 一輪(預設冪等跳過)")
    ap.add_argument("--ph-only", action="store_true",
                    help="只回補 Product Hunt(需環境變數 PH_TOKEN;設計上在 GitHub Actions 跑)")
    args = ap.parse_args()
    print(f"回補範圍:{BACKFILL_DATES[0]} ～ {BACKFILL_DATES[-1]}(絕不觸碰 {TODAY} 的既有列)")
    print()
    if args.ph_only:
        backfill_ph(force=args.force)
        print()
        print("PH 回補完成。")
        return
    backfill_github(force=args.force)
    print()
    backfill_hn(force=args.force)
    print()
    backfill_openrouter(force=args.force)
    print()
    backfill_ollama(force=args.force)
    print()
    skip_ph_hf()
    print()
    print("回補完成。")


if __name__ == "__main__":
    main()
