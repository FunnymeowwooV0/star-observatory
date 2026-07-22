#!/usr/bin/env python3
"""
GitHub x Hugging Face 觀星台 — 抓榜 + 產出資料庫檔案

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

import html as _html

import requests
from bs4 import BeautifulSoup

import sources_extra as se

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 trend-radar-bot"
TZ = dt.timezone(dt.timedelta(hours=8))  # 台北時間
GH_TOP = 10
HF_TOP = 5


# ----------------------------- 解析(純函式,可離線測試) -----------------------------
def parse_github_trending(html, top=GH_TOP):
    """把 GitHub Trending 頁面 HTML 解析成 list[dict],已按新增星由多到少。解析不到丟例外。"""
    soup = BeautifulSoup(html, "html.parser")
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
    return out[:top]


def parse_hf_trending(data, kind, top=HF_TOP):
    """把 HF trending API 的 JSON 解析成 list[dict]。kind: 'model'|'dataset'|'space'。"""
    items = data.get("recentlyTrending", [])
    out = []
    for it in items[:top]:
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


# ----------------------------- 連網(薄 wrapper,呼叫上面的純解析) -----------------------------
def fetch_github_trending(period):
    """period: 'daily' | 'weekly'. 抓官方 Trending 頁再解析。抓失敗丟例外。"""
    url = f"https://github.com/trending?since={period}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_github_trending(resp.text)


def fetch_hf_trending(kind):
    """kind: 'model'|'dataset'|'space'. 呼叫官方 API 再解析。抓失敗丟例外。"""
    url = f"https://huggingface.co/api/trending?type={kind}&limit={HF_TOP}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_hf_trending(resp.json(), kind)


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


def md_hn_table(items):
    lines = [f"### 📰 Hacker News 頭版 Top {len(items)}(按分數)", "",
             "| # | 標題 | ▲ 分數 | 💬 留言 | 連結 |",
             "|:-:|------|-------:|-------:|:----:|"]
    for i, it in enumerate(items, 1):
        title = (it["title"] or "").replace("|", "/").strip()[:72]
        pts = f"{it['points']:,}" if isinstance(it["points"], int) else "—"
        cmts = f"{it['comments']:,}" if isinstance(it["comments"], int) else "—"
        lines.append(f"| {i} | [{title}]({it['url']}) | {pts} | {cmts} | [HN]({it['hn_url']}) |")
    return "\n".join(lines)


def md_openrouter_table(items):
    lines = [f"### 🧮 OpenRouter 最新一日模型用量 Top {len(items)}", "",
             "| # | 模型 | Σ tokens | prompt | completion |",
             "|:-:|------|---------:|-------:|-----------:|"]
    for i, it in enumerate(items, 1):
        lines.append(f"| {i} | {it['model']} | {it['total_tokens']:,} | "
                     f"{it['prompt_tokens']:,} | {it['completion_tokens']:,} |")
    return "\n".join(lines)


def md_ph_table(items):
    lines = [f"### 🚀 Product Hunt AI 主題 24h 票選 Top {len(items)}", "",
             "| # | 名稱 | 一句話 | ▲ 票數 |",
             "|:-:|------|--------|-------:|"]
    for i, it in enumerate(items, 1):
        name = (it["name"] or "").replace("|", "/").strip()
        tag = (it["tagline"] or "").replace("|", "/").strip()[:72]
        votes = f"{it['votes']:,}" if isinstance(it["votes"], int) else "—"
        lines.append(f"| {i} | [{name}]({it['url']}) | {tag} | {votes} |")
    return "\n".join(lines)


PH_SKIP_NOTE = "### 🚀 Product Hunt AI 主題 24h 票選\n\n> Product Hunt:未設 token 略過(本機無環境變數 `PH_TOKEN`)。"


ABOUT = """> 🛰️ **觀星台** — 每天/每週自動抓 GitHub 與 Hugging Face 的熱門榜,存成可累積的資料庫。
> 資料來源:GitHub Trending(官方頁)＋ Hugging Face Trending API(官方)。由 GitHub Actions 自動更新,不需開電腦。
> 想看歷史:[`archive/`](archive/)(每月一檔)、[`weekly/`](weekly/)、[`data/`](data/)(CSV)。
"""


# ----------------------------- 產網頁 dashboard(靜態 HTML,零 AI) -----------------------------
def _esc(s):
    return _html.escape(str(s if s is not None else ""))


SITE_CSS = """
:root{--bg:#f7f7f4;--fg:#1a1a19;--mut:#6b6b66;--card:#ffffff;--line:#e3e2db;--acc:#0f6e56}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#f2f2ee;--mut:#a3a29a;--card:#1f1f1d;--line:#2f2f2c;--acc:#5dcaa5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang TC","Microsoft JhengHei",sans-serif;line-height:1.6}
.topbar{height:6px;background:linear-gradient(90deg,#0f6e56,#d85a30,#185fa5,#eda100,#534ab7)}
header,section,footer{max-width:1080px;margin:0 auto;padding:0 24px}
header{padding-top:32px;padding-bottom:8px}
.kicker{font-size:12px;letter-spacing:.12em;color:var(--acc);font-weight:600}
h1{font-size:30px;margin:6px 0 8px;font-weight:700}
h2{font-size:20px;margin:34px 0 14px;font-weight:600}
h3{font-size:15px;margin:0 0 10px;font-weight:600}
.sub{color:var(--mut);font-size:14px;margin:0 0 4px;max-width:760px}
.err{max-width:1080px;margin:12px auto;padding:10px 16px;background:#fbeaea;color:#791f1f;border-radius:8px;font-size:14px}
.bars{display:flex;flex-direction:column;gap:8px}
.bar-row{display:grid;grid-template-columns:220px 1fr 72px;align-items:center;gap:12px;font-size:13px}
.bar-name{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{background:var(--line);border-radius:6px;height:16px;overflow:hidden}
.bar-fill{display:block;height:100%;background:var(--acc);border-radius:6px}
.bar-val{text-align:right;font-variant-numeric:tabular-nums;color:var(--mut)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:6px}
.card-top{display:flex;justify-content:space-between;font-size:12px;color:var(--mut)}
.rank{color:var(--acc);font-weight:700}
.card-name{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;font-weight:600;word-break:break-all}
.card-stars{font-size:26px;font-weight:700}.card-stars span{font-size:12px;font-weight:400;color:var(--mut)}
.card-desc{font-size:13px;color:var(--mut);flex:1}
.card-link{font-size:13px;color:var(--acc);text-decoration:none;margin-top:4px}
.hf-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.hf-col{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.hf-list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:10px}
.hf-list li{display:grid;grid-template-columns:20px 1fr;gap:8px;font-size:13px;align-items:baseline}
.hf-list a{grid-column:2;color:var(--fg);text-decoration:none;font-weight:600;word-break:break-all}
.hf-name{grid-column:2;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600;word-break:break-all}
.hf-rank{color:var(--mut);font-variant-numeric:tabular-nums}
.hf-meta{grid-column:2;color:var(--mut);font-size:12px}
footer{color:var(--mut);font-size:12px;padding:32px 24px 48px;border-top:1px solid var(--line);margin-top:40px}
footer code{background:var(--line);padding:1px 5px;border-radius:4px}
@media(max-width:640px){.bar-row{grid-template-columns:130px 1fr 60px}h1{font-size:24px}}
"""


def render_html(date, stamp, gh, hf, errors, hn=None, openrouter=None, ph=None, ph_skipped=False):
    """把當日榜單渲染成一頁自足的靜態 HTML(不含 AI,純資料排版)。"""
    hn = hn or []
    openrouter = openrouter or []
    ph = ph or []
    max_stars = max([r["period_stars"] or 0 for r in gh] + [1])
    bars = ""
    for r in gh:
        ps = r["period_stars"] or 0
        w = max(4, round(ps / max_stars * 100))
        bars += (f'<div class="bar-row"><span class="bar-name">{_esc(r["name"])}</span>'
                 f'<span class="bar-track"><span class="bar-fill" style="width:{w}%"></span></span>'
                 f'<span class="bar-val">+{ps:,}</span></div>')
    cards = ""
    for i, r in enumerate(gh[:5], 1):
        cards += (f'<div class="card"><div class="card-top"><span class="rank">#{i:02d}</span>'
                  f'<span>{_esc(r["lang"] or "—")} · {(r["total_stars"] or 0):,} 總星</span></div>'
                  f'<div class="card-name">{_esc(r["name"])}</div>'
                  f'<div class="card-stars">+{(r["period_stars"] or 0):,}<span> 今日新增星</span></div>'
                  f'<div class="card-desc">{_esc(r["desc"])[:120]}</div>'
                  f'<a class="card-link" href="{_esc(r["url"])}" target="_blank" rel="noopener">GitHub 倉庫 →</a></div>')

    def hf_block(label, items, prefix):
        rows = ""
        for i, it in enumerate(items, 1):
            likes = f'{it["likes"]:,}' if isinstance(it["likes"], int) else "—"
            dls = f'{it["downloads"]:,}' if isinstance(it["downloads"], int) else "—"
            tag = f' · {_esc(it["tag"])}' if it["tag"] else ""
            rows += (f'<li><span class="hf-rank">{i}</span>'
                     f'<a href="{_esc(it["url"])}" target="_blank" rel="noopener">{_esc(it["id"])}</a>'
                     f'<span class="hf-meta">❤ {likes} · ⬇ {dls}{tag}</span></li>')
        return f'<div class="hf-col"><h3>{prefix} {label} Top {len(items)}</h3><ol class="hf-list">{rows}</ol></div>'

    hf_html = (hf_block("模型", hf.get("模型", []), "🔥")
               + hf_block("資料集", hf.get("資料集", []), "📚")
               + hf_block("Spaces", hf.get("Spaces", []), "🚀"))

    def list_block(prefix, label, items, render_item):
        rows = "".join(render_item(i, it) for i, it in enumerate(items, 1))
        return (f'<div class="hf-col"><h3>{prefix} {label} Top {len(items)}</h3>'
                f'<ol class="hf-list">{rows}</ol></div>')

    def hn_item(i, it):
        pts = f'{it["points"]:,}' if isinstance(it["points"], int) else "—"
        cmts = f'{it["comments"]:,}' if isinstance(it["comments"], int) else "—"
        return (f'<li><span class="hf-rank">{i}</span>'
                f'<a href="{_esc(it["url"])}" target="_blank" rel="noopener">{_esc(it["title"])}</a>'
                f'<span class="hf-meta">▲ {pts} · 💬 {cmts} · '
                f'<a href="{_esc(it["hn_url"])}" target="_blank" rel="noopener">HN 討論</a></span></li>')

    def or_item(i, it):
        return (f'<li><span class="hf-rank">{i}</span>'
                f'<span class="hf-name">{_esc(it["model"])}</span>'
                f'<span class="hf-meta">Σ {it["total_tokens"]:,} tokens · '
                f'prompt {it["prompt_tokens"]:,} · completion {it["completion_tokens"]:,}</span></li>')

    def ph_item(i, it):
        votes = f'{it["votes"]:,}' if isinstance(it["votes"], int) else "—"
        tag = f' — {_esc(it["tagline"])}' if it["tagline"] else ""
        return (f'<li><span class="hf-rank">{i}</span>'
                f'<a href="{_esc(it["url"])}" target="_blank" rel="noopener">{_esc(it["name"])}</a>'
                f'<span class="hf-meta">▲ {votes}{tag}</span></li>')

    hn_section = (f'<section><h2>📰 Hacker News 頭版</h2>'
                  f'<p class="sub">HN 當前頭版按分數(Algolia 官方 API)。</p>'
                  f'<div class="hf-cols">{list_block("📰", "頭版", hn, hn_item)}</div></section>') if hn else ""
    or_section = (f'<section><h2>🧮 OpenRouter 模型用量</h2>'
                  f'<p class="sub">OpenRouter 官方排行資料(非官方文件端點),最新一日模型用量。</p>'
                  f'<div class="hf-cols">{list_block("🧮", "模型用量", openrouter, or_item)}</div></section>') if openrouter else ""
    if ph:
        ph_section = (f'<section><h2>🚀 Product Hunt</h2>'
                      f'<p class="sub">Product Hunt AI 主題 24h 票選。</p>'
                      f'<div class="hf-cols">{list_block("🚀", "AI 榜", ph, ph_item)}</div></section>')
    elif ph_skipped:
        ph_section = ('<section><h2>🚀 Product Hunt</h2>'
                      '<p class="sub">Product Hunt AI 主題 24h 票選 — 未設 token 略過(本機無環境變數 <code>PH_TOKEN</code>)。</p></section>')
    else:
        ph_section = ""

    err_html = ('<div class="err">⚠️ 部分來源抓取失敗:' + "；".join(_esc(e) for e in errors) + "</div>") if errors else ""
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>觀星台 · {date}</title><style>{SITE_CSS}</style></head><body>'
            f'<div class="topbar"></div>'
            f'<header><div class="kicker">GITHUB TRENDING 日榜 · HUGGING FACE 熱門</div>'
            f'<h1>{date} · GitHub × Hugging Face 熱門觀測</h1>'
            f'<p class="sub">以 GitHub Trending 日榜的「今日新增星」作 24 小時成長代理值,合併 Hugging Face 官方熱門 API。更新於 {stamp}。</p></header>'
            f'{err_html}'
            f'<section><h2>🐙 GitHub 24 小時成長排行 Top {len(gh)}</h2><div class="bars">{bars}</div></section>'
            f'<section><h2>焦點前五</h2><div class="cards">{cards}</div></section>'
            f'<section><h2>🤗 Hugging Face 本日熱門</h2><div class="hf-cols">{hf_html}</div></section>'
            f'{hn_section}{or_section}{ph_section}'
            f'<footer>資料來源:GitHub Trending 日榜 + Hugging Face 官方 API(<code>/api/trending</code>)。'
            f'「今日新增星」為 GitHub 提供之當日動能,作 24 小時成長代理值。'
            f'本頁由觀星台腳本每日自動產生(純資料、無 AI);白話說明與發想在 Obsidian 每週導讀。</footer>'
            f'</body></html>')


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

    # 新資料源:HN / OpenRouter / Product Hunt(各自 try/except 進 errors,仿 HF 寫法)
    attempted = 4  # GitHub(1) + HF(3);下面每嘗試一個抓取單元就 +1(PH 無 token 的 skip 不計入)

    hn = []
    attempted += 1
    try:
        hn = se.fetch_hn_frontpage()
    except Exception as e:  # noqa: BLE001
        errors.append(f"HN 頭版抓取失敗:{e}")

    openrouter = []
    attempted += 1
    try:
        openrouter = se.fetch_openrouter_rankings()
    except Exception as e:  # noqa: BLE001
        errors.append(f"OpenRouter 用量抓取失敗:{e}")

    ph = []
    ph_skipped = False
    ph_token = os.environ.get("PH_TOKEN")
    if not ph_token:
        ph_skipped = True  # 沒 token 屬預期 skip,不算 error、不計入 attempted
    else:
        attempted += 1
        try:
            ph = se.fetch_ph_posts(ph_token)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Product Hunt 抓取失敗:{e}")

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
    if hn:
        parts.append(md_hn_table(hn))
    if openrouter:
        parts.append(md_openrouter_table(openrouter))
    if ph:
        parts.append(md_ph_table(ph))
    elif ph_skipped:
        parts.append(PH_SKIP_NOTE)
    if errors:
        parts.append("> ⚠️ 本次部分來源抓取失敗:\n>\n" + "\n".join(f"> - {e}" for e in errors))
    body = "\n\n".join(parts)

    # README(覆蓋)
    readme = f"# 📈 觀星台\n\n{ABOUT}\n\n---\n\n## 今日榜單 · {date}\n_更新於 {stamp}_\n\n{body}\n"
    write_text("README.md", readme)

    # 網頁 dashboard(靜態 HTML,覆蓋;供 GitHub Pages)
    write_text("docs/index.html",
               render_html(date, stamp, gh, hf, errors, hn, openrouter, ph, ph_skipped))

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

    # 新資料源 CSV(新檔,不動既有 schema)
    append_csv("data/hn_daily.csv",
               ["date", "rank", "title", "points", "comments", "hn_url", "url"],
               [[date, i + 1, h["title"], h["points"], h["comments"], h["hn_url"], h["url"]]
                for i, h in enumerate(hn)])
    append_csv("data/openrouter_daily.csv",
               ["date", "rank", "model", "total_tokens", "prompt_tokens", "completion_tokens"],
               [[date, i + 1, m["model"], m["total_tokens"], m["prompt_tokens"], m["completion_tokens"]]
                for i, m in enumerate(openrouter)])
    append_csv("data/ph_daily.csv",
               ["date", "rank", "name", "tagline", "votes", "url"],
               [[date, i + 1, p["name"], p["tagline"], p["votes"], p["url"]]
                for i, p in enumerate(ph)])

    # Issue 內文(= email)
    write_text(".issue_body.md",
               f"每日自動彙整 · {date}(台北時間)\n\n{body}\n\n---\n完整歷史見 repo 的 archive/ 與 data/。")

    print(f"[daily] done. GitHub={len(gh)} HF模型={len(hf.get('模型',[]))} "
          f"資料集={len(hf.get('資料集',[]))} Spaces={len(hf.get('Spaces',[]))} "
          f"HN={len(hn)} OpenRouter={len(openrouter)} "
          f"PH={'skip' if ph_skipped else len(ph)} "
          f"errors={len(errors)}/{attempted}")
    return errors, attempted


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
    if args.mode == "daily":
        errors, attempted = run_daily(now)
        # 全部抓取單元都掛才視為失敗(讓 Action 紅燈);部分失敗仍出榜但退場碼 0。
        # attempted 隨啟用的資料源動態變化,不寫死數字;PH 無 token 的 skip 不計入。
        if attempted and len(errors) >= attempted:
            print("ERROR: 所有來源都抓取失敗", file=sys.stderr)
            sys.exit(1)
    else:
        errors = run_weekly(now)
        if errors and len(errors) >= 1:
            print("ERROR: 每週榜抓取失敗", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
