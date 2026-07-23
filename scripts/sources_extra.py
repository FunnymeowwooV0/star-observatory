#!/usr/bin/env python3
"""
新資料源(HN + OpenRouter + Product Hunt + Ollama + Reddit)— 純解析函式 + 薄連網 wrapper。

刻意與 fetch_trends.py 分檔:後者的 render_html()/SITE_CSS 之後要改版面,
把新源的程式集中在這裡可減少撞檔。風格比照 fetch_trends.py 的
parse_hf_trending()(純解析、可離線測)/ fetch_hf_trending()(薄連網)。

五源口徑:
    - HN         Algolia 官方 API,當前頭版按 points 取前 10(不爬 HTML)
    - OpenRouter 官方排行資料(非官方文件端點),最新一日模型用量 Top 10(脆弱源:解析不到丟例外)
    - Product Hunt 官方 GraphQL,過去 24h AI 主題貼文 Top 10(token 只從環境變數,無 token 由呼叫端 skip)
    - Ollama    公開 HTML(ollama.com/library?sort=popular),popular 榜前 10(脆弱源:解析不到丟例外)
                每模型帶官方一句描述;Pulls 累計數,delta 由 compute_pull_deltas 純函式算「今日新增」
    - Reddit    公開 HTML(old.reddit.com/r/LocalLLaMA/top/?t=week),週榜前 10(脆弱源:解析不到丟例外)
                **不進 GitHub Actions**(資料中心 IP 可能被擋),只給本機每週導讀 CLI 用,見
                scripts/reddit_weekly.py 與 goals/05-reddit週熱帖.md。
"""
import datetime as dt
import re

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 trend-radar-bot"
HN_TOP = 10
OR_TOP = 10
PH_TOP = 10
OL_TOP = 10
REDDIT_TOP = 10


# ----------------------------- 解析(純函式,可離線測試) -----------------------------
def parse_hn_frontpage(data, top=HN_TOP):
    """把 HN Algolia search API 的 JSON 解析成 list[dict],按 points 由多到少。

    空/缺 hits → 回空列表(不丟例外;HN 是官方穩定源)。
    外部 url 可能為空 → 退回 HN 討論頁連結。
    """
    hits = data.get("hits", []) or []
    out = []
    for h in hits:
        oid = h.get("objectID")
        hn_url = f"https://news.ycombinator.com/item?id={oid}" if oid else ""
        out.append({
            "title": h.get("title") or "",
            "points": h.get("points"),
            "comments": h.get("num_comments"),
            "hn_url": hn_url,
            "url": h.get("url") or hn_url,
        })
    out.sort(key=lambda x: x["points"] if x["points"] is not None else -1, reverse=True)
    return out[:top]


def parse_openrouter_rankings(data, top=OR_TOP, target_date=None):
    """把 OpenRouter 內部 rankings API 的 JSON 解析成 list[dict]。

    預設(target_date=None):取回資料中「最新一日」的所有 rows。
    target_date(選配,'YYYY-MM-DD' 字串):改取該日的 rows(給回補腳本重用,
    rankings API 一次回傳的資料本就含最近數天;不影響預設行為)。
    同一 model 的多個 variant 會加總 prompt+completion tokens,
    再按 total_tokens 由多到少取前 top。

    脆弱源:比照 GitHub Trending,解析不到就丟例外,**不得靜默回空榜**。
    """
    rows = data.get("data")
    if not rows:
        raise RuntimeError("OpenRouter rankings 沒解析到任何資料(內部 API 可能改版了)")
    if target_date is not None:
        picked = target_date
    else:
        picked = max(r.get("date") or "" for r in rows)
    if not picked:
        raise RuntimeError("OpenRouter rankings 解析不到日期欄位(內部 API 可能改版了)")
    agg = {}
    for r in rows:
        if not (r.get("date") or "").startswith(picked):
            continue
        model = r.get("model_permaslug")
        if model is None:
            raise RuntimeError("OpenRouter rankings 缺 model_permaslug 欄位(內部 API 可能改版了)")
        e = agg.setdefault(model, {"model": model, "prompt_tokens": 0, "completion_tokens": 0})
        e["prompt_tokens"] += r.get("total_prompt_tokens") or 0
        e["completion_tokens"] += r.get("total_completion_tokens") or 0
    if not agg:
        raise RuntimeError("OpenRouter rankings 最新一日沒有任何模型 rows(內部 API 可能改版了)")
    out = []
    for e in agg.values():
        e["total_tokens"] = e["prompt_tokens"] + e["completion_tokens"]
        out.append(e)
    out.sort(key=lambda x: x["total_tokens"], reverse=True)
    return out[:top]


def parse_ph_posts(data, top=PH_TOP):
    """把 Product Hunt GraphQL posts(...) 的回傳 JSON 解析成 list[dict],按票數由多到少。

    空/缺欄位 → 回空列表(呼叫端負責在沒 token 時 skip)。
    """
    edges = (((data or {}).get("data") or {}).get("posts") or {}).get("edges", []) or []
    out = []
    for edge in edges:
        node = edge.get("node") or {}
        out.append({
            "name": node.get("name") or "",
            "tagline": node.get("tagline") or "",
            "votes": node.get("votesCount"),
            "url": node.get("url") or "",
        })
    out.sort(key=lambda x: x["votes"] if x["votes"] is not None else -1, reverse=True)
    return out[:top]


def _parse_pulls(s):
    """把 Ollama 的 Pulls 縮寫字串轉成 int。K=1e3 / M=1e6 / B=1e9,含小數與千分位逗號。

    例:'117.5M'→117500000、'79.3K'→79300、'1.2B'→1200000000、'900'→900。
    解析不到數字回 None。
    """
    m = re.search(r"([\d.,]+)\s*([KMB]?)", s or "")
    if not m or not m.group(1):
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
    return int(round(num * mult))


def parse_ollama_library(html, top=OL_TOP):
    """把 Ollama 模型庫 popular 頁面 HTML 解析成 list[dict]。

    **榜序=頁面原序**(sort=popular 是官方口徑),不自己按 Pulls 重排。
    脆弱源(爬 HTML,同 GitHub Trending):解析不到任何模型 → 丟例外,不得靜默回空榜。

    每筆:name / url / desc(官方一句描述)/ pulls(int,縮寫已展開)/
          caps(能力標籤 list,如 tools/thinking/embedding,可為空)/ updated(原字串)。
    caps 判定:標籤裡「不含數字」的才是能力標籤;含數字的是尺寸(8b/70b/270m/e2b)→ 排除。
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select('li a[href^="/library/"]')
    if not anchors:
        raise RuntimeError("Ollama library 頁面沒解析到任何模型(版面可能改了)")
    out = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        if "/library/" not in href:
            continue
        name = href.split("/library/", 1)[1].strip("/")
        if not name:
            continue
        desc_p = a.select_one("p")
        desc = desc_p.get_text(strip=True) if desc_p else ""
        tagdiv = a.select_one("div.flex.flex-wrap")
        tags = [s.get_text(strip=True) for s in tagdiv.select("span")] if tagdiv else []
        caps = [t for t in tags if t and not any(c.isdigit() for c in t)]
        ps = a.select("p")
        stats = ps[-1].get_text(" ", strip=True) if ps else ""
        pm = re.search(r"([\d.,]+)\s*([KMB]?)\s*Pulls", stats)
        pulls = _parse_pulls(pm.group(1) + pm.group(2)) if pm else None
        um = re.search(r"Updated\s+(.+)$", stats)
        updated = um.group(1).strip() if um else ""
        out.append({
            "name": name,
            "url": f"https://ollama.com/library/{name}",
            "desc": desc,
            "pulls": pulls,
            "caps": caps,
            "updated": updated,
        })
    if not out:
        raise RuntimeError("Ollama library 解析不到任何有效模型(選擇器可能壞了)")
    return out[:top]


def parse_reddit_top(html, top=REDDIT_TOP):
    """把 old.reddit r/LocalLLaMA 週榜頁面 HTML 解析成 list[dict],榜序=頁面原序(官方 top/week 排序)。

    脆弱源(爬 HTML,同 GitHub Trending/Ollama):解析不到任何貼文 → 丟例外,不得靜默回空榜。

    每筆:title / score(int,「•」隱藏分數或空 → None)/ comments(int,解析不到 → None)/
          permalink(**正規化成 https://www.reddit.com/... 給讀者點**,內部抓取仍用 old.reddit)/
          external_url(連結帖的外部連結;自帖(data-url 等於自己的 permalink)→ None)。
    """
    soup = BeautifulSoup(html, "html.parser")
    things = soup.select("div.thing[data-permalink]")
    if not things:
        raise RuntimeError("Reddit 週榜頁面沒解析到任何貼文(版面可能改了)")
    out = []
    for t in things[:top]:
        permalink_path = (t.get("data-permalink") or "").strip()
        if not permalink_path:
            continue
        title_a = t.select_one("a.title")
        title = title_a.get_text(strip=True) if title_a else ""

        score_raw = (t.get("data-score") or "").strip()
        try:
            score = int(score_raw)
        except ValueError:
            score = None

        comments_raw = (t.get("data-comments-count") or "").strip()
        try:
            comments = int(comments_raw)
        except ValueError:
            comments = None

        data_url = (t.get("data-url") or "").strip()
        external_url = None
        if data_url and data_url != permalink_path and not data_url.startswith("/r/"):
            external_url = data_url

        out.append({
            "title": title,
            "score": score,
            "comments": comments,
            "permalink": f"https://www.reddit.com{permalink_path}",
            "external_url": external_url,
        })
    if not out:
        raise RuntimeError("Reddit 週榜頁面貼文都缺 permalink,解析不到有效資料(版面可能改了)")
    return out


def compute_pull_deltas(today_items, csv_rows, today):
    """純函式:算每個模型的「今日新增下載」(pulls_delta)。

    today_items:今日榜(list[dict],需含 name / pulls)。
    csv_rows   :data/ollama_daily.csv 既有列(list[dict],需含 date / model / pulls)。
    today      :今天日期字串 'YYYY-MM-DD'。

    先前快照 = **日期嚴格小於 today** 的最近一個日期;delta = 今日 pulls − 該日 pulls。
    找不到先前快照(首日/新進榜/該模型從未出現)→ None(不編造)。
    同日重跑防呆:csv 裡「等於 today」的列**不得**被當基準(只取 date < today)。
    回傳 dict:name → delta(int 或 None)。
    """
    prior_dates = [r.get("date", "") for r in csv_rows if r.get("date", "") < today]
    if not prior_dates:
        return {it["name"]: None for it in today_items}
    prior = max(prior_dates)
    base = {}
    for r in csv_rows:
        if r.get("date") != prior:
            continue
        try:
            base[r["model"]] = int(r["pulls"])
        except (ValueError, TypeError, KeyError):
            pass
    out = {}
    for it in today_items:
        b = base.get(it["name"])
        p = it.get("pulls")
        out[it["name"]] = (p - b) if (b is not None and p is not None) else None
    return out


# ----------------------------- 連網(薄 wrapper,呼叫上面的純解析) -----------------------------
def fetch_hn_frontpage():
    """抓 HN 頭版(Algolia 官方 API)再解析。抓失敗丟例外。"""
    url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_hn_frontpage(resp.json())


def fetch_openrouter_rankings():
    """抓 OpenRouter 每日模型用量排行(內部 API)再解析。抓/解析失敗丟例外。"""
    url = "https://openrouter.ai/api/frontend/v1/rankings/models"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_openrouter_rankings(resp.json())


def fetch_ph_posts(token, posted_after=None, posted_before=None):
    """用 GraphQL 抓 AI 主題貼文再解析。token 由呼叫端從環境變數帶入。

    預設(兩個時間參數皆 None)=平常每日抓法:過去 24h,行為與加參數前完全相同。
    歷史回補用:帶 posted_after/posted_before(ISO8601 'YYYY-MM-DDTHH:MM:SSZ')查該窗。
    ⚠️ 口徑:votesCount 是「查詢當下」的票數;回補歷史時屬事後快照,非當日票數。

    抓失敗或 GraphQL 回 errors → 丟例外。requests 內建即可 POST GraphQL,不需新相依。
    """
    url = "https://api.producthunt.com/v2/api/graphql"
    if posted_after is None:
        posted_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if posted_before is None:
        query = """
        query($after: DateTime!, $first: Int!) {
          posts(topic: "artificial-intelligence", order: VOTES, postedAfter: $after, first: $first) {
            edges { node { name tagline votesCount url } }
          }
        }
        """
        variables = {"after": posted_after, "first": PH_TOP}
    else:
        query = """
        query($after: DateTime!, $before: DateTime!, $first: Int!) {
          posts(topic: "artificial-intelligence", order: VOTES, postedAfter: $after, postedBefore: $before, first: $first) {
            edges { node { name tagline votesCount url } }
          }
        }
        """
        variables = {"after": posted_after, "before": posted_before, "first": PH_TOP}
    resp = requests.post(
        url,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": UA},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"Product Hunt GraphQL 回傳 errors:{payload['errors']}")
    return parse_ph_posts(payload)


def fetch_ollama_library():
    """抓 Ollama 模型庫 popular 頁(公開 HTML,免金鑰)再解析。抓/解析失敗丟例外。"""
    url = "https://ollama.com/library?sort=popular"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_ollama_library(resp.text)


def fetch_reddit_top():
    """抓 old.reddit r/LocalLLaMA 週榜(公開 HTML,免金鑰)再解析。抓/解析失敗丟例外。

    刻意用 old.reddit(`.json` 後門與 www 版都被擋,見 goals/05-reddit週熱帖.md 的踩坑紀錄)。
    只給本機呼叫(住宅 IP);不進 GitHub Actions(資料中心 IP 可能被擋)。
    """
    url = "https://old.reddit.com/r/LocalLLaMA/top/?t=week"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return parse_reddit_top(resp.text)
