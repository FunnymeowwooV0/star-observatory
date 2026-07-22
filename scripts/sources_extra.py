#!/usr/bin/env python3
"""
新資料源三合一(HN + OpenRouter + Product Hunt)— 純解析函式 + 薄連網 wrapper。

刻意與 fetch_trends.py 分檔:後者的 render_html()/SITE_CSS 之後要改版面,
把三個新源的程式集中在這裡可減少撞檔。風格比照 fetch_trends.py 的
parse_hf_trending()(純解析、可離線測)/ fetch_hf_trending()(薄連網)。

三源口徑:
    - HN         Algolia 官方 API,當前頭版按 points 取前 10(不爬 HTML)
    - OpenRouter 官方排行資料(非官方文件端點),最新一日模型用量 Top 10(脆弱源:解析不到丟例外)
    - Product Hunt 官方 GraphQL,過去 24h AI 主題貼文 Top 10(token 只從環境變數,無 token 由呼叫端 skip)
"""
import datetime as dt

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 trend-radar-bot"
HN_TOP = 10
OR_TOP = 10
PH_TOP = 10


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


def parse_openrouter_rankings(data, top=OR_TOP):
    """把 OpenRouter 內部 rankings API 的 JSON 解析成 list[dict]。

    取回資料中「最新一日」的所有 rows,按 model 合計 prompt+completion tokens
    (同一 model 的多個 variant 會加總),再按 total_tokens 由多到少取前 top。

    脆弱源:比照 GitHub Trending,解析不到就丟例外,**不得靜默回空榜**。
    """
    rows = data.get("data")
    if not rows:
        raise RuntimeError("OpenRouter rankings 沒解析到任何資料(內部 API 可能改版了)")
    latest = max(r.get("date") or "" for r in rows)
    if not latest:
        raise RuntimeError("OpenRouter rankings 解析不到日期欄位(內部 API 可能改版了)")
    agg = {}
    for r in rows:
        if (r.get("date") or "") != latest:
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


def fetch_ph_posts(token):
    """用 GraphQL 抓過去 24h AI 主題貼文再解析。token 由呼叫端從環境變數帶入。

    抓失敗或 GraphQL 回 errors → 丟例外。requests 內建即可 POST GraphQL,不需新相依。
    """
    url = "https://api.producthunt.com/v2/api/graphql"
    posted_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = """
    query($after: DateTime!, $first: Int!) {
      posts(topic: "artificial-intelligence", order: VOTES, postedAfter: $after, first: $first) {
        edges { node { name tagline votesCount url } }
      }
    }
    """
    resp = requests.post(
        url,
        json={"query": query, "variables": {"after": posted_after, "first": PH_TOP}},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": UA},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"Product Hunt GraphQL 回傳 errors:{payload['errors']}")
    return parse_ph_posts(payload)
