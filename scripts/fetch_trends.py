#!/usr/bin/env python3
"""
GitHub x Hugging Face 趨勢雷達 — 抓榜 + 產出資料庫檔案

用法:
    python scripts/fetch_trends.py --mode daily     # 每日:GitHub 當日新增星 Top10 + HF 各 Top5
    python scripts/fetch_trends.py --mode weekly    # 每週:GitHub 當週新增星 Top10

資料來源:
    - GitHub Trending  https://github.com/trending?since=daily|weekly   (官方頁面,按「期間新增星」排序;無官方 API,故解析 HTML)
    - Hugging Face     https://huggingface.co/api/trending?type=model|dataset|space   (官方 API)

產出:
    README.md               今日/本週最新快照(每次覆蓋)
    archive/YYYY-MM.md      當月每天往下追加(每月一檔)
    weekly/YYYY-Www.md      每週榜
    data/*.csv              機器可讀歷史
    .issue_body.md          給 GitHub Action 拿去開 Issue(=email)的內文
"""
import argparse
import csv
import datetime as dt
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 trend-radar-bot"
TZ = dt.timezone(dt.timedelta(hours=8))  # 台北時間
GH_TOP = 10
HF_TOP = 5


# ----------------------------- 抓資料 -----------------------------
def fetch_github_trending(period):
    """period: 'daily' | 'weekly'. 回傳 list[dict],已按新增星由多到少。抓失敗丟例外。"""
    url = f"https://github.com/trending?since={period}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("article.Box-row")
    if not rows:
        raise RuntimeError("GitHub Trending 頁面沒有解析到任何 repo(版面可能改了)")
    out = []
    for r in rows:
        a = r.select_one("h2 a")
        if not a:
            continue
        name = " ".join(a.get_text().split()).replace(" / ", "/")
        href = a.get("href", "").strip()
        repo_url = "https://github.com" + href
        p = r.select_one("p")
        desc = p.get_text(strip=True) if p else ""
        lang_el = r.select_one("[itemprop=programmingLanguage]")
        lang = lang_el.get_text(strip=True) if lang_el else ""
        fsr = r.select_one("span.float-sm-right")
        period_stars = _parse_int(fsr.get_text()) if fsr else None
        total_stars = None
        muted = r.select("a.Link--muted")
        for m in muted:
            if "/stargazers" in (m.get("href") or ""):
                total_stars = _parse_int(m.get_text())
                break
        out.append({
            "name": name, "url": repo_url, "desc": desc, "lang": lang,
            "period_stars": period_stars, "total_stars": total_stars,
        })
    # 已按新增星排序;保險起見再排一次(None 當 -1)
    out.sort(key=lambda x: (x["period_stars"] if x["period_stars"] is not None else -1), reverse=True)
    return out[:GH_TOP]


def fetch_hf_trending(kind):
    """kind: 'model'|'dataset'|'space'. 回傳 list[dict]。抓失敗丟例外。"""
    url = f"https://huggingface.co/api/trending?type={kind}&limit={HF_TOP}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("recentlyTrending", [])
    out = []
    for it in items[:HF_TOP]:
        rd = it.get("repoData", {})
        rid = rd.get("id", "?")
        out.append({
            "id": rid,
            "url": f"https://huggingface.co/{'' if kind=='model' else kind+'s/'}{rid}",
            "likes": rd.get("likes"),
            "downloads": rd.get("downloads"),
            "tag": rd.get("pipeline_tag") or rd.get("ai_category") or "",
            "title": rd.get("title") or "",
        })
    return out


def _parse_int(s):
    m = re.search(r"[\d,]+", s or "")
    return int(m.group().replace(",", "")) if m else None


# ----------------------------- 產 Markdown -----------------------------
def md_github_table(repos, period_word):
    lines = [f"### 🐙 GitHub 本{period_word}新增星 Top {len(repos)}", "",
             "| # | Repo | ⭐ 新增 | 總星 | 語言 | 說明 |",
             "|:-:|------|-------:|-----:|:----:|------|"]
    for i, r in enumerate(repos, 1):
        ps = f"+{r['period_stars']:,}" if r["period_stars"] is not None else "?"
        ts = f"{r['total_stars']:,}" if r["total_stars"] is not None else "?"
        desc = (r["desc"] or "").replace("|", "/").strip()[:72]
        lang = r["lang"] or "—"
        lines.append(f"| {i} | [{r['name']}]({r['url']}) | {ps} | {ts} | {lang} | {desc} |")
    return "\n".join(lines)


def md_hf_table(items, label):
    lines = [f"### 🤗 Hugging Face 熱門{label} Top {len(items)}", "",
             "| # | 名稱 | ❤️ Likes | ⬇️ 下載 | 類型 |",
             "|:-:|------|--------:|-------:|:----:|"]
    for i, it in enumerate(items, 1):
        likes = f"{it['likes']:,}" if isinstance(it["likes"], int) else "—"
        dls = f"{it['downloads']:,}" if isinstance(it["downloads"], int) else "—"
        tag = it["tag"] or "—"
        lines.append(f"| {i} | [{it['id']}]({it['url']}) | {likes} | {dls} | {tag} |")
    return "\n".join(lines)


ABOUT = """> 🛰️ **趨勢雷達** — 每天/每週自動抓 GitHub 與 Hugging Face 的熱門榜,存成可累積的資料庫。
> 資料來源:GitHub Trending(官方頁)＋ Hugging Face Trending API(官方)。由 GitHub Actions 自動更新,不需開電腦。
> 想看歷史:[`archive/`](archive/)(每月一檔)、[`weekly/`](weekly/)、[`data/`](data/)(CSV)。
"""


# ----------------------------- 檔案寫入 -----------------------------
def write_text(path, text):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def append_text(path, text):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(text)


def append_csv(path, header, rows):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    exists = os.path.exists(full)
    with open(full, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        w.writerows(rows)


# ----------------------------- 主流程 -----------------------------
def run_daily(now):
    date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M %Z")
    errors = []

    gh = []
    try:
        gh = fetch_github_trending("daily")
    except Exception as e:  # noqa: BLE001
        errors.append(f"GitHub 每日榜抓取失敗:{e}")

    hf = {}
    for kind, label in [("model", "模型"), ("dataset", "資料集"), ("space", "Spaces")]:
        try:
            hf[label] = fetch_hf_trending(kind)
        except Exception as e:  # noqa: BLE001
            errors.append(f"HF {label}榜抓取失敗:{e}")
            hf[label] = []

    # 組 markdown 內文
    parts = []
    if gh:
        parts.append(md_github_table(gh, "日"))
    if hf.get("模型"):
        parts.append(md_hf_table(hf["模型"], "模型"))
    if hf.get("資料集"):
        parts.append(md_hf_table(hf["資料集"], "資料集"))
    if hf.get("Spaces"):
        parts.append(md_hf_table(hf["Spaces"], "Spaces"))
    if errors:
        parts.append("> ⚠️ 本次部分來源抓取失敗:\n>\n" + "\n".join(f"> - {e}" for e in errors))
    body = "\n\n".join(parts)

    # README(覆蓋)
    readme = f"# 📈 趨勢雷達\n\n{ABOUT}\n\n---\n\n## 今日榜單 · {date}\n_更新於 {stamp}_\n\n{body}\n"
    write_text("README.md", readme)

    # 當月彙整(追加)
    month = now.strftime("%Y-%m")
    append_text(f"archive/{month}.md",
                f"\n## {date}\n_更新於 {stamp}_\n\n{body}\n\n---\n")

    # CSV
    append_csv("data/github_daily.csv",
               ["date", "rank", "repo", "new_stars", "total_stars", "language", "url", "description"],
               [[date, i + 1, r["name"], r["period_stars"], r["total_stars"], r["lang"], r["url"], r["desc"]]
                for i, r in enumerate(gh)])
    hf_rows = []
    for label, items in hf.items():
        for i, it in enumerate(items):
            hf_rows.append([date, label, i + 1, it["id"], it["likes"], it["downloads"], it["tag"], it["url"]])
    append_csv("data/hf_trending.csv",
               ["date", "type", "rank", "id", "likes", "downloads", "tag", "url"], hf_rows)

    # Issue 內文(= email)
    write_text(".issue_body.md",
               f"每日自動彙整 · {date}(台北時間)\n\n{body}\n\n---\n完整歷史見 repo 的 archive/ 與 data/。")

    print(f"[daily] done. GitHub={len(gh)} HF模型={len(hf.get('模型',[]))} "
          f"資料集={len(hf.get('資料集',[]))} Spaces={len(hf.get('Spaces',[]))} errors={len(errors)}")
    return errors


def run_weekly(now):
    date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M %Z")
    iso = now.isocalendar()
    wk = f"{iso.year}-W{iso.week:02d}"
    errors = []

    gh = []
    try:
        gh = fetch_github_trending("weekly")
    except Exception as e:  # noqa: BLE001
        errors.append(f"GitHub 每週榜抓取失敗:{e}")

    parts = [md_github_table(gh, "週")] if gh else []
    if errors:
        parts.append("> ⚠️ " + "；".join(errors))
    body = "\n\n".join(parts)

    write_text(f"weekly/{wk}.md",
               f"# 📅 本週 GitHub 新增星 Top {GH_TOP} · {wk}\n_更新於 {stamp}_\n\n{body}\n")
    append_csv("data/github_weekly.csv",
               ["week", "date", "rank", "repo", "new_stars", "total_stars", "language", "url", "description"],
               [[wk, date, i + 1, r["name"], r["period_stars"], r["total_stars"], r["lang"], r["url"], r["desc"]]
                for i, r in enumerate(gh)])
    write_text(".issue_body.md",
               f"每週自動彙整 · {wk}({date} 台北時間)\n\n{body}\n\n---\n完整歷史見 repo 的 weekly/ 與 data/。")

    print(f"[weekly] done. GitHub={len(gh)} errors={len(errors)}")
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly"], required=True)
    args = ap.parse_args()
    now = dt.datetime.now(TZ)
    errors = run_daily(now) if args.mode == "daily" else run_weekly(now)
    # 全部來源都掛才視為失敗(讓 Action 紅燈);部分失敗仍出榜但退場碼 0
    if errors and args.mode == "daily" and len(errors) >= 4:
        print("ERROR: 所有來源都抓取失敗", file=sys.stderr)
        sys.exit(1)
    if errors and args.mode == "weekly" and len(errors) >= 1:
        print("ERROR: 每週榜抓取失敗", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
