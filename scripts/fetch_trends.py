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
from urllib.parse import quote

import html as _html

import requests
from bs4 import BeautifulSoup

import sources_extra as se
import history as hi

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


def md_ollama_table(items, deltas):
    lines = [f"### 🦙 Ollama 熱門模型 Top {len(items)}", "",
             "| # | 模型 | Pulls(累計) | 今日新增 | 功能 |",
             "|:-:|------|----------:|--------:|------|"]
    for i, it in enumerate(items, 1):
        pulls = f"{it['pulls']:,}" if isinstance(it["pulls"], int) else "—"
        d = deltas.get(it["name"]) if deltas else None
        delta = f"{d:+,}" if isinstance(d, int) else "—"
        desc = (it["desc"] or "").replace("|", "/").strip()[:72]
        lines.append(f"| {i} | [{it['name']}]({it['url']}) | {pulls} | {delta} | {desc} |")
    return "\n".join(lines)


PH_SKIP_NOTE = "### 🚀 Product Hunt AI 主題 24h 票選\n\n> Product Hunt:未設 token 略過(本機無環境變數 `PH_TOKEN`)。"


ABOUT = """> 🛰️ **觀星台** — 每天/每週自動抓 GitHub 與 Hugging Face 的熱門榜,存成可累積的資料庫。
> 資料來源:GitHub Trending(官方頁)＋ Hugging Face Trending API(官方)。由 GitHub Actions 自動更新,不需開電腦。
> 想看歷史:[`archive/`](archive/)(每月一檔)、[`weekly/`](weekly/)、[`data/`](data/)(CSV)。
"""


# ----------------------------- 產網頁 dashboard(靜態 HTML,零 AI) -----------------------------
def _esc(s):
    return _html.escape(str(s if s is not None else ""))


GH_CATEGORY_RULES = (
    ("AI／LLM", {"ai", "llm", "model", "agent", "rag", "embedding", "prompt", "voice", "speech"}),
    ("資料與監測", {"monitor", "monitoring", "dashboard", "analytics", "observability", "tracking",
                 "intelligence", "visualization"}),
    ("開發工具", {"developer", "code", "coding", "review", "debug", "test", "cli", "sdk", "api", "git",
              "terminal"}),
    ("生產力", {"productivity", "task", "todo", "notes", "calendar", "adhd", "workflow", "automation"}),
    ("基礎設施", {"infrastructure", "devops", "deploy", "deployment", "cloud", "server", "proxy", "router",
              "container", "kubernetes"}),
    ("網站／應用", {"web", "app", "platform", "ecommerce", "browser", "mobile", "social"}),
)


def infer_github_category(name, desc):
    """以 repo 名稱與公開簡介的完整英數 token，依固定優先序推定單一用途。"""
    tokens = set(re.findall(r"[a-z0-9]+", f"{name or ''} {desc or ''}".lower()))
    for label, keywords in GH_CATEGORY_RULES:
        if tokens & keywords:
            return label
    return "其他工具"


def _openrouter_model_url(model):
    model = str(model or "").strip()
    return f"https://openrouter.ai/{quote(model, safe='/')}" if model else ""


def _link_card_body(i, title, meta, mark="", desc=""):
    desc_html = f'<span class="link-desc">{_esc(desc)}</span>' if desc else ""
    return (f'<span class="link-rank">{i:02d}</span><span class="link-body">'
            f'<span class="link-title">{_esc(title)}{mark}</span>'
            f'<span class="link-meta">{meta}</span>{desc_html}</span>')


def _link_card_item(i, title, meta, url, mark="", desc=""):
    """輸出整列連結卡；沒有目的地時保留同版式但不產生空連結。"""
    body = _link_card_body(i, title, meta, mark, desc)
    if url:
        return (f'<li class="link-card-item"><a class="link-card" href="{_esc(url)}" '
                f'target="_blank" rel="noopener">{body}</a></li>')
    return f'<li class="link-card-item"><span class="link-card link-card-static">{body}</span></li>'


SITE_CSS = """
:root{color-scheme:light dark;--sky-top:#08206b;--sky-mid:#07184f;--galaxy:#1760e8;--dusk-mauve:#66506d;--dusk-apricot:#d7865b;--paper:#f2e9d2;--ink:#111319;--ivory:#f5edda;--star:#e5c43a;--sky-text:#f5edda;--sky-muted:#c0cbea;--paper-muted:#5c5b59;--line:rgba(17,19,25,.2);--line-strong:#3157a6;--hover:#fff8e7;--nav:#081b5a;--acc:#e5c43a;--up:#0f6e56;--down:#b23b3b;--new:#185fa5}
@media(prefers-color-scheme:dark){:root{--sky-top:#061747;--sky-mid:#030d2f;--dusk-mauve:#51415f;--dusk-apricot:#b96649;--paper:#eadfc7;--ivory:#f0e6cf;--sky-muted:#aebddd;--nav:#05133f;--hover:#f6ecd5}}
*{box-sizing:border-box}[hidden]{display:none!important}
html{background:var(--sky-mid);scroll-behavior:smooth}
body{position:relative;isolation:isolate;min-height:100vh;margin:0;color:var(--sky-text);font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;line-height:1.55;background:linear-gradient(180deg,var(--sky-top) 0%,var(--sky-mid) calc(100% - 190px),var(--dusk-mauve) calc(100% - 84px),var(--dusk-apricot) 100%);overflow-x:clip}
body::before{content:"";position:absolute;z-index:0;inset:0 0 auto;height:min(560px,62vh);pointer-events:none;background:radial-gradient(circle at 12% 7%,rgba(229,196,58,.95) 0 1px,transparent 3px),radial-gradient(circle at 34% 5%,rgba(245,237,218,.85) 0 1px,transparent 3px),radial-gradient(circle at 63% 10%,rgba(229,196,58,.8) 0 1px,transparent 4px),radial-gradient(circle at 84% 4%,rgba(245,237,218,.9) 0 1px,transparent 4px),radial-gradient(ellipse 64% 46% at -6% 72%,rgba(23,96,232,.28),transparent 70%),radial-gradient(ellipse 58% 42% at 58% 18%,rgba(30,104,246,.25),transparent 72%),radial-gradient(ellipse 52% 38% at 106% 8%,rgba(26,91,230,.3),transparent 70%);mix-blend-mode:screen}
body::after{content:"";position:absolute;z-index:0;inset:0;pointer-events:none;opacity:.08;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.78' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");mix-blend-mode:soft-light}
body>*{position:relative;z-index:1}
.sky-art{position:absolute;z-index:0;inset:0;overflow:hidden;pointer-events:none}
.sky-art::before,.sky-art::after{content:"";position:absolute;border-style:solid;border-color:rgba(26,99,240,.28);border-radius:50%;filter:blur(28px);mix-blend-mode:screen}
.sky-art::before{width:118%;height:420px;left:-38%;top:150px;border-width:82px;transform:rotate(-11deg)}
.sky-art::after{width:82%;height:350px;right:-34%;top:-170px;border-width:66px;transform:rotate(-23deg);opacity:.86}
.orbit{position:absolute;width:290px;height:118px;border:1px solid rgba(245,237,218,.2);border-radius:50%}
.orbit::before,.orbit::after{content:"";position:absolute;border:1px solid rgba(245,237,218,.15);border-radius:inherit}
.orbit::before{inset:-18px}.orbit::after{inset:-39px;opacity:.65}
.orbit-top{top:24px;right:-92px;transform:rotate(-12deg)}
.orbit-bottom{bottom:36px;left:-126px;transform:rotate(17deg)}
a,select{touch-action:manipulation;-webkit-tap-highlight-color:rgba(229,196,58,.2)}
a{color:var(--star)}a:hover{text-decoration-thickness:2px;text-underline-offset:3px}a:active{opacity:.76}
a:focus-visible,.date-switch:focus-visible{outline:3px solid var(--star);outline-offset:3px;border-radius:4px}
.skip-link{position:absolute;z-index:10;left:16px;top:-64px;background:var(--paper);color:var(--ink);padding:10px 14px}
.skip-link:focus{top:10px}
.topbar{height:2px;background:var(--star)}
header,main,footer,.page-nav,.subnav-wrap{max-width:1240px;margin:0 auto;padding-left:24px;padding-right:24px}
header{padding-top:34px;padding-bottom:6px}
.kicker{font-size:12px;letter-spacing:.14em;color:var(--star);font-weight:600}
h1,h2,.rank{font-family:"Noto Serif TC","Songti TC","PMingLiU",serif}
h1{font-size:42px;line-height:1.24;margin:8px 0 10px;font-weight:600;letter-spacing:.01em;text-wrap:balance}
h2{font-size:28px;line-height:1.3;margin:24px 0 8px;font-weight:600;letter-spacing:.01em;text-wrap:balance}
h3{font-size:15px;margin:0 0 10px;font-weight:600;color:var(--sky-text)}
h4{font-size:15px;margin:0 0 8px;font-weight:600;color:var(--sky-text)}
.heading-icon{margin-right:.35em}
.sub{color:var(--sky-muted);font-size:13px;margin:0 0 6px;max-width:920px}
.nav-shell{position:sticky;top:0;z-index:8;margin-top:18px;border-top:1px solid rgba(245,237,218,.24);border-bottom:1px solid rgba(245,237,218,.24);background:var(--nav)}
.page-nav{display:flex;align-items:center;flex-wrap:wrap;gap:6px 18px;min-height:58px}
.tab-list{display:flex;align-items:center;border:1px solid rgba(245,237,218,.48);border-radius:8px;overflow:hidden}
.page-tab{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:7px 18px;border-left:1px solid rgba(245,237,218,.34);color:var(--sky-text);font-size:14px;font-weight:600;text-decoration:none}
.page-tab:first-child{border-left:0}
.page-tab[aria-selected="true"]{background:var(--paper);color:var(--ink)}
.page-tab:hover{text-decoration:none;background:rgba(245,237,218,.1)}
.page-tab[aria-selected="true"]:hover{background:var(--ivory);color:var(--ink)}
.history-picker{display:flex;align-items:center;gap:8px;min-height:44px;color:var(--sky-muted);font-size:13px}
.history-picker label{font-weight:600;color:var(--sky-text)}
.date-switch{min-height:38px;font-size:13px;padding:5px 30px 5px 10px;border-radius:6px;border:1px solid rgba(245,237,218,.48);background:var(--nav);color:var(--sky-text)}
.subnav-wrap{padding-bottom:9px}
.context-tabs{display:flex;min-width:0;overflow-x:auto;overscroll-behavior-inline:contain;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.context-tabs::-webkit-scrollbar{display:none}
.sub-tab{display:inline-flex;flex:0 0 auto;min-height:44px;align-items:center;justify-content:center;padding:8px 18px;border:1px solid rgba(245,237,218,.46);border-left-width:0;color:var(--sky-text);font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.sub-tab:first-child{border-left-width:1px;border-radius:6px 0 0 6px}.sub-tab:last-child{border-radius:0 6px 6px 0}
.sub-tab[aria-selected="true"]{background:var(--paper);color:var(--ink)}
.sub-tab:hover{text-decoration:none;background:rgba(245,237,218,.1)}
.sub-tab[aria-selected="true"]:hover{background:var(--ivory);color:var(--ink)}
section,.tab-panel{scroll-margin-top:132px}
.source-panel,.lb-period{padding:12px 0 34px}
.err{margin:14px 0;padding:11px 16px;background:#f5d8d1;color:#6f1d1d;border:1px solid rgba(111,29,29,.25);border-radius:6px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 14px;margin-top:14px}
.card{display:grid;grid-template-columns:64px minmax(0,1fr) minmax(128px,auto);gap:12px;align-items:center;min-width:0;min-height:98px;padding:12px 16px;background:var(--paper);border:1px solid var(--line);border-radius:8px;color:var(--ink);text-decoration:none;cursor:pointer;overflow-wrap:anywhere}
.rank{font-size:34px;line-height:1;color:#12388b;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.card-content{display:flex;min-width:0;flex-direction:column;gap:4px}
.card-heading-row{display:flex;min-width:0;align-items:center;flex-wrap:wrap;gap:4px 8px}
.card-name{margin:0;font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;font-weight:600;overflow-wrap:anywhere}
.category{display:inline-flex;align-items:center;padding:1px 6px;border:1px solid rgba(18,56,139,.5);border-radius:4px;color:#12388b;font-size:11px;font-weight:600;white-space:nowrap}
.card-desc{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;margin:0;font-size:12px;color:var(--paper-muted);line-height:1.5}
.card-metrics{display:flex;min-width:0;flex-direction:column;gap:6px;border-left:1px solid rgba(17,19,25,.22);padding-left:12px;font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.card-meta{min-width:0;color:var(--paper-muted);font-size:11px}
.card-stars{font-size:20px;line-height:1.15;font-weight:600;color:#8a6500}.card-stars span{font-family:"Noto Sans TC",sans-serif;font-size:11px;font-weight:500;color:var(--paper-muted)}
.hf-cols{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 28px;margin-top:14px}
.source-panel>.hf-cols{grid-template-columns:1fr}
.hf-col{min-width:0;background:transparent;border:0;border-radius:0;padding:0}
.source-note{margin:0 0 10px;color:var(--sky-muted);font-size:12px;line-height:1.5}
.link-card-list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px}
.link-card-item{min-width:0}
.link-card{width:100%;min-width:0;min-height:54px;display:grid;grid-template-columns:34px minmax(0,1fr);gap:10px;align-items:center;padding:9px 12px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);text-decoration:none;cursor:pointer;overflow-wrap:anywhere}
.link-card-static{cursor:default}
.link-rank{color:#12388b;font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;font-variant-numeric:tabular-nums}
.link-body{display:flex;min-width:0;flex-direction:column;gap:2px}
.link-title{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:600;overflow-wrap:anywhere}
.link-meta,.link-desc{color:var(--paper-muted);font-size:11px;line-height:1.5}
.hn-card-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:stretch}
.hn-discussion{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:8px 13px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink);font-size:12px;font-weight:600;text-decoration:none;white-space:nowrap}
.card,.link-card,.hn-discussion{transition:transform 140ms cubic-bezier(.2,0,0,1),border-color 140ms cubic-bezier(.2,0,0,1),background-color 140ms cubic-bezier(.2,0,0,1)}
@media(hover:hover) and (pointer:fine){.card:hover,.link-card:not(.link-card-static):hover,.hn-discussion:hover{transform:translateY(-2px) scale(1.005);border-color:var(--line-strong);background:var(--hover);text-decoration:none}}
.card:active,.link-card:not(.link-card-static):active,.hn-discussion:active{transform:translateY(0) scale(.995);opacity:1}
footer{color:var(--sky-muted);font-size:12px;padding-top:28px;padding-bottom:46px;border-top:1px solid rgba(245,237,218,.24);margin-top:34px}
footer code{background:rgba(245,237,218,.16);padding:1px 5px;border-radius:3px}
.mark{font-size:11px;font-weight:700;margin-left:6px}.card .mark{margin-left:0}
.mark.up{color:var(--up)}.mark.down{color:var(--down)}.mark.new{color:var(--new)}.mark.same{color:var(--paper-muted);font-weight:400}
header .mark.up{color:#83d9bb}header .mark.down{color:#ffaaa6}header .mark.new{color:#a9ceff}header .mark.same{color:var(--sky-muted)}
.link-title .mark{margin-left:6px}
.spark{display:block;margin-top:3px}.spark rect{fill:var(--acc)}
.snapshot-banner{max-width:1240px;margin:16px auto 0;padding:10px 16px;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:6px;font-size:14px}
.snapshot-banner a{color:#12388b;font-weight:700}
.period-note{margin:10px 0 16px}.period-note strong{color:var(--sky-text);font-family:"Noto Serif TC",serif;font-size:18px}
@media(max-width:900px){.cards{grid-template-columns:1fr}.hf-cols{grid-template-columns:1fr}}
@media(max-width:640px){
  header,main,footer,.page-nav,.subnav-wrap{padding-left:16px;padding-right:16px}
  header{padding-top:24px}h1{font-size:29px;line-height:1.28}h2{font-size:24px;margin-top:18px}
  .title-date,.title-topic{display:block}
  .nav-shell{margin-top:12px}.page-nav{align-items:stretch;gap:6px 12px;padding-top:8px;padding-bottom:7px}
  .tab-list{flex:1}.page-tab{min-width:0;flex:1;padding-left:10px;padding-right:10px}
  .history-picker{flex-basis:100%;justify-content:space-between}.date-switch{flex:1;max-width:220px;min-height:44px}
  .subnav-wrap{padding-bottom:8px}.sub-tab{padding-left:15px;padding-right:15px}
  section,.tab-panel{scroll-margin-top:174px}.source-panel,.lb-period{padding-top:8px;padding-bottom:26px}
  .card{grid-template-columns:48px minmax(0,1fr);min-height:132px;padding:12px;gap:8px 10px}
  .rank{font-size:29px;align-self:start;padding-top:2px}
  .card-metrics{grid-column:1/-1;flex-direction:row;align-items:flex-end;justify-content:space-between;border-left:0;border-top:1px solid rgba(17,19,25,.2);padding:8px 0 0}
  .card-stars{text-align:right}.card-name{font-size:13px}.card-desc{font-size:12px}
  .hf-cols{grid-template-columns:1fr}.card,.link-card,.hn-discussion,.page-tab,.sub-tab,.snapshot-banner a{min-height:44px}
  .hn-card-row{grid-template-columns:1fr}.hn-discussion{width:100%}
  .orbit-top{right:-180px}.orbit-bottom{left:-190px}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}.card,.link-card,.hn-discussion{transform:none!important}}
"""


TAB_SCRIPT = r"""
<script data-tab-controller>
(() => {
  const lists = Array.from(document.querySelectorAll('[data-tab-group][role="tablist"]'));
  if (!lists.length) return;
  const byGroup = new Map(lists.map(list => [list.dataset.tabGroup, list]));
  const tabsFor = group => {
    const list = byGroup.get(group);
    return list ? Array.from(list.querySelectorAll(':scope > [role="tab"]')) : [];
  };
  const targetId = tab => tab.getAttribute('aria-controls');
  const targetPanel = tab => {
    const panel = document.getElementById(targetId(tab));
    return panel && panel.matches('[data-tab-panel]') ? panel : null;
  };
  const samePage = tab => {
    const url = new URL(tab.getAttribute('href'), window.location.href);
    return url.origin === window.location.origin && url.pathname === window.location.pathname;
  };
  const tabFor = (group, id) => tabsFor(group).find(tab => targetId(tab) === id && targetPanel(tab));
  const selectedId = group => {
    const selected = tabsFor(group).find(tab => tab.getAttribute('aria-selected') === 'true');
    return selected ? targetId(selected) : null;
  };
  const parentPanel = group => {
    const list = byGroup.get(group);
    return list ? list.getAttribute('data-parent-panel') : null;
  };
  const historyTargetId = (group, activeTab) => {
    const ownId = targetId(activeTab);
    if (group !== 'page') return ownId;
    const childList = lists.find(list => list.getAttribute('data-parent-panel') === ownId);
    return childList ? (selectedId(childList.dataset.tabGroup) || ownId) : ownId;
  };

  function select(group, id) {
    const tabs = tabsFor(group);
    const activeTab = tabFor(group, id) || tabs[0];
    if (!activeTab) return null;
    tabs.forEach(tab => {
      const selected = tab === activeTab;
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      tab.setAttribute('tabindex', selected ? '0' : '-1');
      const panel = targetPanel(tab);
      if (panel) panel.hidden = !selected;
    });
    return activeTab;
  }

  function syncContext(pageId) {
    lists.filter(list => list.hasAttribute('data-parent-panel')).forEach(list => {
      list.hidden = list.getAttribute('data-parent-panel') !== pageId;
    });
  }

  function activate(group, id, {write = false, scroll = false, focus = false} = {}) {
    const parent = parentPanel(group);
    if (parent) select('page', parent);
    const activeTab = select(group, id);
    if (!activeTab) return;
    const pageId = parent || (group === 'page' ? targetId(activeTab) : selectedId('page'));
    if (!pageId) return;
    syncContext(pageId);
    if (write) {
      const nextHash = `#${historyTargetId(group, activeTab)}`;
      if (window.location.hash !== nextHash) window.history.pushState(null, '', nextHash);
    }
    if (focus) activeTab.focus();
    if (scroll) {
      const panel = targetPanel(activeTab);
      const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
      window.requestAnimationFrame(() => panel.scrollIntoView({block: 'start', behavior}));
    }
  }

  lists.forEach(list => {
    const group = list.dataset.tabGroup;
    const tabs = tabsFor(group);
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => {
        if (!samePage(tab)) return;
        event.preventDefault();
        activate(group, targetId(tab), {write: true, scroll: true});
      });
      tab.addEventListener('keydown', event => {
        if (event.key === ' ' || (event.key === 'Enter' && samePage(tab))) {
          event.preventDefault();
          tab.click();
          return;
        }
        let nextIndex = null;
        if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') nextIndex = 0;
        if (event.key === 'End') nextIndex = tabs.length - 1;
        if (nextIndex === null) return;
        event.preventDefault();
        const nextTab = tabs[nextIndex];
        if (samePage(nextTab)) {
          activate(group, targetId(nextTab), {write: true, scroll: true, focus: true});
        } else {
          nextTab.focus();
          nextTab.click();
        }
      });
    });
  });

  function syncFromLocation({scroll = false} = {}) {
    const id = window.location.hash.slice(1);
    if (tabFor('source', id)) return activate('source', id, {scroll});
    if (tabFor('period', id)) return activate('period', id, {scroll});
    const pageId = tabFor('page', id) ? id : 'today';
    activate('page', pageId, {scroll});
  }

  lists.forEach(list => select(list.dataset.tabGroup, selectedId(list.dataset.tabGroup)));
  window.addEventListener('popstate', () => syncFromLocation({scroll: true}));
  window.addEventListener('hashchange', () => syncFromLocation({scroll: true}));
  syncFromLocation();
})();
</script>
"""


# ----------------------------- 歷史比較:標記/趨勢線/累積榜(薄 glue,呼叫 history.py 純函式) -----------------------------
def _build_marks(dedup_rows, name_col, today, today_ids):
    """回 dict identity→(kind,n) tuple(=history.rank_changes 的原始輸出),只含 today_ids 裡的項目。"""
    prior_date = hi.find_prior_date(dedup_rows, today)
    prior_ids = [r.get(name_col) for r in dedup_rows if r.get("date") == prior_date] if prior_date else []
    return hi.rank_changes(list(today_ids), prior_ids)


def _mark_html(change):
    if not change:
        return ""
    kind, _n = change
    return f'<span class="mark {kind}">{_esc(hi.format_rank_change(change))}</span>'


def _build_sparks(dedup_rows, name_col, value_col, today, ids, diff=False):
    """回 dict identity→SVG 字串(資料不足時 history.svg_sparkline 已回空字串,直接沿用)。"""
    out = {}
    for name in ids:
        series = hi.spark_series(dedup_rows, name_col, value_col, name, today, days=7, diff=diff)
        out[name] = hi.svg_sparkline(series)
    return out


def _lb_item_github(i, e):
    days = f' · 上榜 {e["days"]} 天' if e.get("days") else ""
    return _link_card_item(i, e["name"], f'Σ +{e["value"]:,} ⭐{days}', e.get("url") or "")


def _lb_item_openrouter(i, e):
    return _link_card_item(i, e["name"], f'Σ {e["value"]:,} tokens', _openrouter_model_url(e["name"]))


def _lb_item_hf(i, e):
    likes, dls = e.get("likes_delta"), e.get("downloads_delta")
    likes_s = f'+{likes:,}' if isinstance(likes, int) else '—'
    metrics = [f'Likes {likes_s}']
    if isinstance(dls, int):
        metrics.append(f'下載 +{dls:,}')
    return _link_card_item(i, e["name"], " · ".join(metrics), e.get("url") or "")


def _lb_item_ollama(i, e):
    d = e.get("pulls_delta")
    d_s = f'+{d:,}' if isinstance(d, int) else '—'
    return _link_card_item(i, e["name"], f'⬇ {d_s} pulls', e.get("url") or "")


def _lb_item_hn(i, e):
    v = e.get("value")
    v_s = f'{v:,}' if isinstance(v, int) else '—'
    return _link_card_item(i, e["name"], f'▲ {v_s}(期間最高分)', e.get("url") or "")


def _lb_item_ph(i, e):
    v = e.get("value")
    v_s = f'{v:,}' if isinstance(v, int) else '—'
    return _link_card_item(i, e["name"], f'▲ {v_s}(期間最高票)', e.get("url") or "")


# (source_key, 標題, emoji, item renderer, Top N)
LEADERBOARD_SOURCES = [
    ("github", "GitHub 累積新增星", "🐙", _lb_item_github, 10),
    ("openrouter", "OpenRouter 用量", "🧮", _lb_item_openrouter, 5),
    ("hf_model", "HF 模型 讚/下載增量", "🔥", _lb_item_hf, 5),
    ("hf_dataset", "HF 資料集 讚/下載增量", "📚", _lb_item_hf, 5),
    ("hf_space", "HF Spaces 讚/下載增量", "🚀", _lb_item_hf, 5),
    ("ollama", "Ollama 下載增量", "🦙", _lb_item_ollama, 5),
    ("hn", "HN 期間最高分", "📰", _lb_item_hn, 5),
    ("ph", "PH 期間最高票", "🚀", _lb_item_ph, 5),
]


def _lb_block(emoji, title, items, render_item, top):
    top_items = items[:top]
    rows = "".join(render_item(i, e) for i, e in enumerate(top_items, 1))
    return (f'<div class="hf-col"><h4><span class="heading-icon" aria-hidden="true">{_esc(emoji)}</span>'
            f'{_esc(title)} Top {len(top_items)}</h4><ol class="link-card-list">{rows}</ol></div>')


def _accumulation_section(leaderboards):
    """leaderboards:{"week":{...},"month":{...}},每個 period → {"start":..,"days":..,"sources":{key:[...]}}。

    誠實界線(見設計稿):每個 period 區塊都標「統計自…起(N 天)」+「僅統計進過每日 Top N 的項目」。
    """
    if not leaderboards:
        return "", ""
    tabs, panels = [], []
    for period_key, period_label in (("week", "本週"), ("month", "本月")):
        pdata = leaderboards.get(period_key) or {}
        sources = pdata.get("sources") or {}
        cols = [_lb_block(emoji, title, sources[key], render_item, top)
                for key, title, emoji, render_item, top in LEADERBOARD_SOURCES
                if sources.get(key)]
        if not cols:
            continue
        start, days = pdata.get("start", "?"), pdata.get("days", "?")
        note = (f'<p class="sub period-note"><strong>{period_label}</strong> · '
                f'統計自 {_esc(start)} 起（資料庫目前累積 {days} 天資料）。'
                f'僅統計進過每日 Top N 的項目——這是快照資料庫的天生限制，不是全網統計。</p>')
        panel_id = f"leaderboards-{period_key}"
        tab_id = f"period-tab-{period_key}"
        selected = not tabs
        selected_value = "true" if selected else "false"
        tab_index = "0" if selected else "-1"
        tabs.append(
            f'<a id="{tab_id}" class="sub-tab period-tab" role="tab" href="#{panel_id}" '
            f'aria-controls="{panel_id}" aria-selected="{selected_value}" '
            f'tabindex="{tab_index}">{period_label}</a>'
        )
        panels.append(
            f'<section id="{panel_id}" class="lb-period" role="tabpanel" '
            f'data-tab-panel="period" aria-labelledby="{tab_id}">{note}'
            f'<div class="hf-cols">{"".join(cols)}</div></section>'
        )
    if not panels:
        return "", ""
    period_tabs = (
        '<div class="context-tabs period-tab-list" role="tablist" '
        'data-tab-group="period" data-parent-panel="leaderboards" aria-label="累積榜期間">'
        + "".join(tabs) + '</div>'
    )
    section = (
        '<section id="leaderboards" class="tab-panel" role="tabpanel" '
        'data-tab-panel="page" aria-labelledby="tab-leaderboards">'
        + "".join(panels) + '</section>'
    )
    return section, period_tabs


def _snapshot_banner_html(snapshot_date):
    if not snapshot_date:
        return ""
    return (f'<div class="snapshot-banner">📅 這是 {_esc(snapshot_date)} 的歷史快照 · '
            f'<a href="../index.html">回今日</a></div>')


def _date_switcher_html(history_dates, snapshot_date):
    """history_dates:所有已產生快照的日期(任意順序)。snapshot_date:None=在 index 頁,
    非 None=在 docs/history/<snapshot_date>.html 頁。GitHub Pages 有路徑前綴,只用相對路徑。"""
    dates = sorted(set(history_dates), reverse=True)
    if not dates:
        return '<span class="history-picker"><span>看歷史</span><span>尚無快照</span></span>'
    opts = ['<option value="" disabled selected>選擇日期…</option>'] if snapshot_date is None else []
    for d in dates:
        href = f"{d}.html" if snapshot_date is not None else f"history/{d}.html"
        sel = " selected" if d == snapshot_date else ""
        opts.append(f'<option value="{_esc(href)}"{sel}>{_esc(d)}</option>')
    return ('<span class="history-picker"><label for="history-date">看歷史</label>'
            '<select id="history-date" class="date-switch" aria-label="切換日期查看歷史" '
            'onchange="if(this.value) location.href=this.value">' + "".join(opts) + '</select></span>')


def render_html(date, stamp, gh, hf, errors, hn=None, openrouter=None, ph=None, ph_skipped=False,
                ollama=None, ollama_deltas=None, marks=None, sparks=None, leaderboards=None,
                history_dates=None, snapshot_date=None):
    """把當日榜單渲染成一頁自足的靜態 HTML(不含 AI,純資料排版)。"""
    hn = hn or []
    openrouter = openrouter or []
    ph = ph or []
    ollama = ollama or []
    ollama_deltas = ollama_deltas or {}
    marks = marks or {}
    sparks = sparks or {}
    leaderboards = leaderboards or {}
    history_dates = history_dates or []

    gh_marks = marks.get("github", {})
    cards = ""
    for i, r in enumerate(gh[:10], 1):
        raw_desc = (r["desc"] or "").strip()
        desc = raw_desc or "暫無說明"
        period_stars = r["period_stars"] if isinstance(r["period_stars"], int) else 0
        total_stars = f'{r["total_stars"]:,}' if isinstance(r["total_stars"], int) else "—"
        mark = _mark_html(gh_marks.get(r["name"]))
        category = infer_github_category(r["name"], raw_desc)
        cards += (f'<a class="card gh-card" href="{_esc(r["url"])}" target="_blank" rel="noopener">'
                  f'<span class="rank">#{i:02d}</span>'
                  f'<span class="card-content"><span class="card-heading-row">'
                  f'<span class="card-name">{_esc(r["name"])}</span>'
                  f'<span class="category">{_esc(category)}</span>{mark}</span>'
                  f'<span class="card-desc">{_esc(desc)}</span></span>'
                  f'<span class="card-metrics">'
                  f'<span class="card-meta">{total_stars} 總星 · {_esc(r["lang"] or "—")}</span>'
                  f'<span class="card-stars">+{period_stars:,}<span> 今日新增星</span></span>'
                  f'</span></a>')

    def hf_list(items, mark_key, note=""):
        m = marks.get(mark_key, {})
        rows = ""
        for i, it in enumerate(items, 1):
            likes = f'{it["likes"]:,}' if isinstance(it["likes"], int) else "—"
            meta = [f'Likes {likes}']
            if isinstance(it["downloads"], int):
                meta.append(f'下載 {it["downloads"]:,}')
            if it["tag"]:
                meta.append(_esc(it["tag"]))
            mark = _mark_html(m.get(it["id"]))
            rows += _link_card_item(i, it["id"], " · ".join(meta), it["url"], mark)
        note_html = f'<p class="source-note">{_esc(note)}</p>' if note else ""
        return f'{note_html}<ol class="link-card-list">{rows}</ol>'

    def list_block(prefix, label, items, render_item):
        rows = "".join(render_item(i, it) for i, it in enumerate(items, 1))
        return (f'<div class="hf-col"><h3><span class="heading-icon" aria-hidden="true">{_esc(prefix)}</span>'
                f'{_esc(label)} Top {len(items)}</h3>'
                f'<ol class="link-card-list">{rows}</ol></div>')

    def source_panel(panel_id, tab_id, heading, note, body):
        note_html = f'<p class="sub">{_esc(note)}</p>' if note else ""
        return (f'<section id="{panel_id}" class="source-panel" role="tabpanel" '
                f'data-tab-panel="source" aria-labelledby="{tab_id}">'
                f'<h2 id="{panel_id}-title">{_esc(heading)}</h2>{note_html}{body}</section>')

    def hn_item(i, it):
        pts = f'{it["points"]:,}' if isinstance(it["points"], int) else "—"
        cmts = f'{it["comments"]:,}' if isinstance(it["comments"], int) else "—"
        mark = _mark_html(marks.get("hn", {}).get(it["url"]))
        body = _link_card_body(i, it["title"], f'▲ {pts} · 💬 {cmts}', mark)
        url = it.get("url") or ""
        hn_url = it.get("hn_url") or ""
        if url:
            main = (f'<a class="link-card" href="{_esc(url)}" target="_blank" '
                    f'rel="noopener">{body}</a>')
        else:
            main = f'<span class="link-card link-card-static">{body}</span>'
        discussion = ""
        if hn_url and hn_url != url:
            discussion = (f'<a class="hn-discussion" href="{_esc(hn_url)}" target="_blank" '
                          f'rel="noopener">HN 討論</a>')
        return f'<li class="link-card-item hn-card-row">{main}{discussion}</li>'

    def or_item(i, it):
        mark = _mark_html(marks.get("openrouter", {}).get(it["model"]))
        meta = (f'Σ {it["total_tokens"]:,} tokens · prompt {it["prompt_tokens"]:,} · '
                f'completion {it["completion_tokens"]:,}')
        return _link_card_item(i, it["model"], meta, _openrouter_model_url(it["model"]), mark)

    def ph_item(i, it):
        votes = f'{it["votes"]:,}' if isinstance(it["votes"], int) else "—"
        tagline = _esc(it["tagline"]) if it["tagline"] else ""
        meta = f'▲ {votes}' + (f' · {tagline}' if tagline else "")
        mark = _mark_html(marks.get("ph", {}).get(it["url"]))
        return _link_card_item(i, it["name"], meta, it["url"], mark)

    def ol_item(i, it):
        pulls = f'{it["pulls"]:,}' if isinstance(it["pulls"], int) else "—"
        d = ollama_deltas.get(it["name"])
        delta = f'今日 +{d:,}' if isinstance(d, int) else '今日新增 —'
        caps = f' · {_esc(", ".join(it["caps"]))}' if it["caps"] else ""
        mark = _mark_html(marks.get("ollama", {}).get(it["name"]))
        return _link_card_item(i, it["name"], f'⬇ {pulls} Pulls · {delta}{caps}', it["url"], mark,
                               it["desc"])

    hn_body = f'<div class="hf-cols">{list_block("📰", "頭版", hn, hn_item)}</div>' if hn else ""
    or_body = (f'<div class="hf-cols">{list_block("🧮", "模型用量", openrouter, or_item)}</div>'
               if openrouter else "")
    if ph:
        ph_body = f'<div class="hf-cols">{list_block("🚀", "AI 榜", ph, ph_item)}</div>'
        ph_note = "Product Hunt AI 主題 24h 票選。"
    elif ph_skipped:
        ph_body = '<p class="sub">未設 token 略過（本機無環境變數 <code>PH_TOKEN</code>）。</p>'
        ph_note = "Product Hunt AI 主題 24h 票選。"
    else:
        ph_body = ""
        ph_note = ""

    ol_body = f'<div class="hf-cols">{list_block("🦙", "模型", ollama, ol_item)}</div>' if ollama else ""

    source_specs = [
        ("github-focus", "GitHub", f"GitHub 今日焦點 Top {len(gh[:10])}",
         "GitHub Trending 日榜的「今日新增星」是當日動能訊號，作 24 小時動能代理值；非精確的 rolling 24 小時計量。",
         '<p class="sub">用途標籤依專案名稱與公開簡介規則推定。</p>'
         f'<div class="cards">{cards}</div>'),
        ("hf-models", "HF 模型", f'Hugging Face 模型 Top {len(hf.get("模型", []))}',
         "Hugging Face 官方 Trending API 排名；不是依 Likes 或下載量單獨排序。",
         f'<div class="hf-cols"><div class="hf-col">{hf_list(hf.get("模型", []), "hf_model")}</div></div>'),
        ("hf-datasets", "HF 資料集", f'Hugging Face 資料集 Top {len(hf.get("資料集", []))}',
         "Hugging Face 官方 Trending API 排名；不是依 Likes 或下載量單獨排序。",
         f'<div class="hf-cols"><div class="hf-col">{hf_list(hf.get("資料集", []), "hf_dataset")}</div></div>'),
        ("hf-spaces", "HF Spaces", f'Hugging Face 互動應用（Spaces）Top {len(hf.get("Spaces", []))}',
         "Hugging Face 官方 Trending API 排名；不是依 Likes 或下載量單獨排序。",
         f'<div class="hf-cols"><div class="hf-col">{hf_list(hf.get("Spaces", []), "hf_space", "可直接操作的機器學習 Demo／應用，不是模型。")}</div></div>'),
    ]
    if hn_body:
        source_specs.append(("hacker-news", "Hacker News", "Hacker News 頭版",
                             "HN 當前頭版按分數（Algolia 官方 API）。", hn_body))
    if or_body:
        source_specs.append(("openrouter", "OpenRouter", "OpenRouter 模型用量",
                             "OpenRouter 官方排行資料（非官方文件端點），最新一日模型用量。", or_body))
    if ph_body:
        source_specs.append(("product-hunt", "Product Hunt", "Product Hunt", ph_note, ph_body))
    if ol_body:
        source_specs.append((
            "ollama", "Ollama", "Ollama 熱門模型",
            "Ollama 模型庫 popular 榜（頁面原序，未按 Pulls 重排）。Pulls 為累計下載數；「今日新增」由每日快照相減得出，需前一日資料，首日／無先前快照顯示 —。",
            ol_body,
        ))

    source_tabs, source_panels = [], []
    for index, (panel_id, label, heading, note, body) in enumerate(source_specs):
        tab_id = f"source-tab-{panel_id}"
        selected = index == 0
        source_tabs.append(
            f'<a id="{tab_id}" class="sub-tab source-tab" role="tab" href="#{panel_id}" '
            f'aria-controls="{panel_id}" aria-selected="{"true" if selected else "false"}" '
            f'tabindex="{0 if selected else -1}">{_esc(label)}</a>'
        )
        source_panels.append(source_panel(panel_id, tab_id, heading, note, body))
    source_tabs_html = (
        '<div class="context-tabs source-tab-list" role="tablist" data-tab-group="source" '
        'data-parent-panel="today" aria-label="今日榜資料來源">'
        + "".join(source_tabs) + '</div>'
    )

    lb_section, period_tabs_html = _accumulation_section(leaderboards)
    banner_html = _snapshot_banner_html(snapshot_date)
    switcher_html = _date_switcher_html(history_dates, snapshot_date)
    today_href = "../index.html#today" if snapshot_date else "#today"
    leaderboard_tab = ('<a id="tab-leaderboards" class="page-tab" role="tab" href="#leaderboards" '
                       'aria-controls="leaderboards" aria-selected="false" tabindex="-1">累積榜</a>') if lb_section else ""
    nav_html = (f'<div class="nav-shell"><nav class="page-nav" aria-label="主要導覽">'
                f'<div class="tab-list" role="tablist" data-tab-group="page" aria-label="榜單頁面">'
                f'<a id="tab-today" class="page-tab" role="tab" href="{today_href}" '
                f'aria-controls="today" aria-selected="true" tabindex="0">今日榜</a>'
                f'{leaderboard_tab}</div>{switcher_html}</nav>'
                f'<div class="subnav-wrap">{source_tabs_html}{period_tabs_html}</div></div>')

    err_html = ('<div class="err" role="alert">⚠️ 部分來源抓取失敗:'
                + "；".join(_esc(e) for e in errors)
                + '。可稍後重新整理；若持續發生，請查看 Actions 執行紀錄。</div>') if errors else ""
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="#08206b">'
            f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#061747">'
            f'<meta name="description" content="觀星台每日彙整開源、AI 與科技熱門榜單。">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
            f'family=IBM+Plex+Mono:wght@400;500;600&amp;'
            f'family=Noto+Sans+TC:wght@400;500;600;700&amp;'
            f'family=Noto+Serif+TC:wght@500;600;700&amp;display=swap">'
            f'<title>觀星台 · {date}</title><style>{SITE_CSS}</style></head><body>'
            f'<a class="skip-link" href="#main-content">跳至主要內容</a>'
            f'<div class="sky-art" aria-hidden="true">'
            f'<span class="orbit orbit-top"></span><span class="orbit orbit-bottom"></span></div>'
            f'<div class="topbar" aria-hidden="true"></div>'
            f'{banner_html}'
            f'<header><div class="kicker">每日趨勢總覽 · 純資料靜態頁</div>'
            f'<h1><span class="title-date">{date} ·</span> <span class="title-topic">開源與科技熱門觀測</span></h1>'
            f'<p class="sub">彙整 GitHub、Hugging Face、Hacker News、OpenRouter、Product Hunt 與 Ollama 公開榜單。更新於 {stamp}。'
            f'各項目旁 <span class="mark up">↑n</span>/<span class="mark down">↓n</span>/<span class="mark new">NEW</span>/'
            f'<span class="mark same">—</span> 為與昨日(資料裡日期嚴格早於今天的最近一天)排名比較。</p>'
            f'</header>{nav_html}<main id="main-content">'
            f'{err_html}'
            f'<div id="today" class="tab-panel" role="tabpanel" data-tab-panel="page" '
            f'aria-labelledby="tab-today">{"".join(source_panels)}</div>'
            f'{lb_section}</main>'
            f'<footer>資料來源:GitHub Trending、Hugging Face Trending API、Hacker News Algolia、'
            f'OpenRouter 排行資料、Product Hunt API 與 Ollama popular 榜。'
            f'「今日新增星」為 GitHub 提供之當日動能,作 24 小時成長代理值。'
            f'本頁由觀星台腳本每日自動產生(純資料、無 AI);白話說明與發想在 Obsidian 每週導讀。</footer>'
            f'{TAB_SCRIPT}</body></html>')


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


def read_csv_rows(path):
    """讀既有 CSV 成 list[dict](DictReader)。檔案不存在回空列表(首次跑)。"""
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return []
    with open(full, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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

    # Ollama 熱門模型(公開 HTML,零金鑰,一定嘗試)。抓成功後讀既有 CSV 算「今日新增」delta。
    ollama = []
    ollama_deltas = {}
    attempted += 1
    try:
        ollama = se.fetch_ollama_library()
    except Exception as e:  # noqa: BLE001
        errors.append(f"Ollama 熱門模型抓取失敗:{e}")
    if ollama:
        ollama_deltas = se.compute_pull_deltas(ollama, read_csv_rows("data/ollama_daily.csv"), date)

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
    if ollama:
        parts.append(md_ollama_table(ollama, ollama_deltas))
    if errors:
        parts.append("> ⚠️ 本次部分來源抓取失敗:\n>\n" + "\n".join(f"> - {e}" for e in errors))
    body = "\n\n".join(parts)

    # README(覆蓋)
    readme = f"# 📈 觀星台\n\n{ABOUT}\n\n---\n\n## 今日榜單 · {date}\n_更新於 {stamp}_\n\n{body}\n"
    write_text("README.md", readme)

    # 當月彙整(追加)
    month = now.strftime("%Y-%m")
    append_text(f"archive/{month}.md",
                f"\n## {date}\n_更新於 {stamp}_\n\n{body}\n\n---\n")

    # CSV(先寫,history.py 的標記/趨勢線/累積榜要讀到「今天這一輪」才算數)
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
    append_csv("data/ollama_daily.csv",
               ["date", "rank", "model", "pulls", "pulls_delta", "caps", "updated", "url", "desc"],
               [[date, i + 1, it["name"], it["pulls"],
                 (ollama_deltas.get(it["name"]) if isinstance(ollama_deltas.get(it["name"]), int) else ""),
                 ";".join(it["caps"]), it["updated"], it["url"], it["desc"]]
                for i, it in enumerate(ollama)])

    # ---- 歷史比較(讀回剛寫入的 CSV,含今天這一輪)----
    gh_hist = hi.dedupe_daily_rows(read_csv_rows("data/github_daily.csv"), ["rank"])
    hf_hist = hi.dedupe_daily_rows(read_csv_rows("data/hf_trending.csv"), ["type", "rank"])
    hn_hist = hi.dedupe_daily_rows(read_csv_rows("data/hn_daily.csv"), ["rank"])
    or_hist = hi.dedupe_daily_rows(read_csv_rows("data/openrouter_daily.csv"), ["rank"])
    ph_hist = hi.dedupe_daily_rows(read_csv_rows("data/ph_daily.csv"), ["rank"])
    ol_hist = hi.dedupe_daily_rows(read_csv_rows("data/ollama_daily.csv"), ["rank"])
    hf_hist_by_type = {"hf_model": [r for r in hf_hist if r.get("type") == "模型"],
                        "hf_dataset": [r for r in hf_hist if r.get("type") == "資料集"],
                        "hf_space": [r for r in hf_hist if r.get("type") == "Spaces"]}

    marks = {
        "github": _build_marks(gh_hist, "repo", date, [r["name"] for r in gh]),
        "hf_model": _build_marks(hf_hist_by_type["hf_model"], "id", date, [it["id"] for it in hf.get("模型", [])]),
        "hf_dataset": _build_marks(hf_hist_by_type["hf_dataset"], "id", date, [it["id"] for it in hf.get("資料集", [])]),
        "hf_space": _build_marks(hf_hist_by_type["hf_space"], "id", date, [it["id"] for it in hf.get("Spaces", [])]),
        "hn": _build_marks(hn_hist, "url", date, [it["url"] for it in hn]),
        "openrouter": _build_marks(or_hist, "model", date, [it["model"] for it in openrouter]),
        "ph": _build_marks(ph_hist, "url", date, [it["url"] for it in ph]),
        "ollama": _build_marks(ol_hist, "model", date, [it["name"] for it in ollama]),
    }
    sparks = {
        "github": _build_sparks(gh_hist, "repo", "new_stars", date, [r["name"] for r in gh]),
        "ollama": _build_sparks(ol_hist, "model", "pulls_delta", date, [it["name"] for it in ollama]),
        "hf_model": _build_sparks(hf_hist_by_type["hf_model"], "id", "likes", date,
                                   [it["id"] for it in hf.get("模型", [])], diff=True),
        "hf_dataset": _build_sparks(hf_hist_by_type["hf_dataset"], "id", "likes", date,
                                     [it["id"] for it in hf.get("資料集", [])], diff=True),
        "hf_space": _build_sparks(hf_hist_by_type["hf_space"], "id", "likes", date,
                                   [it["id"] for it in hf.get("Spaces", [])], diff=True),
    }

    hist_rows_by_source = {
        "github": gh_hist, "openrouter": or_hist, "ollama": ol_hist, "hn": hn_hist, "ph": ph_hist,
        **hf_hist_by_type,
    }
    leaderboards = {}
    for period in ("week", "month"):
        sources = {}
        for source_key, _title, _emoji, _render, top_n in LEADERBOARD_SOURCES:
            src_rows = hist_rows_by_source.get(source_key, [])
            sources[source_key] = hi.period_leaderboard(src_rows, period, date, source_key, top=top_n)
        # 統計天數以 GitHub(每天必抓)為代表,如實反映資料庫實際累積天數
        start, days = hi.covered_days(gh_hist, period, date)
        leaderboards[period] = {"start": start, "days": days, "sources": sources}

    history_dir = os.path.join(ROOT, "docs", "history")
    existing_snapshots = []
    if os.path.isdir(history_dir):
        existing_snapshots = [fn[:-5] for fn in os.listdir(history_dir) if fn.endswith(".html")]
    history_dates = sorted(set(existing_snapshots) | {date})

    # 網頁 dashboard(靜態 HTML,覆蓋;供 GitHub Pages)+ 當日歷史快照頁(横幅+回今日連結)
    html_common = dict(marks=marks, sparks=sparks, leaderboards=leaderboards, history_dates=history_dates)
    write_text("docs/index.html",
               render_html(date, stamp, gh, hf, errors, hn, openrouter, ph, ph_skipped,
                           ollama, ollama_deltas, snapshot_date=None, **html_common))
    write_text(f"docs/history/{date}.html",
               render_html(date, stamp, gh, hf, errors, hn, openrouter, ph, ph_skipped,
                           ollama, ollama_deltas, snapshot_date=date, **html_common))

    # Issue 內文(= email;v1 範圍鎖:不含累積榜,避免 email 過長)
    write_text(".issue_body.md",
               f"每日自動彙整 · {date}(台北時間)\n\n{body}\n\n---\n完整歷史見 repo 的 archive/ 與 data/。")

    print(f"[daily] done. GitHub={len(gh)} HF模型={len(hf.get('模型',[]))} "
          f"資料集={len(hf.get('資料集',[]))} Spaces={len(hf.get('Spaces',[]))} "
          f"HN={len(hn)} OpenRouter={len(openrouter)} "
          f"PH={'skip' if ph_skipped else len(ph)} Ollama={len(ollama)} "
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
