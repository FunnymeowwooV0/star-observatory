#!/usr/bin/env python3
"""
歷史比較 — 純函式模組(離線可測,零連網)。

給 fetch_trends.py 用,把「日榜 CSV 的歷史」轉成:
    - 同日多輪去重(dedupe_daily_rows)
    - 昨日排名比較標記(rank_changes / format_rank_change)
    - 7 日迷你趨勢線數列 + SVG(spark_series / svg_sparkline)
    - 本週/本月累積排行榜(period_leaderboard)

誠實界線(設計稿 06-歷史比較_設計.md v2):
    - 累積榜「僅統計進過每日 Top N 的項目」— 快照資料庫的天生限制,不假裝是全網統計。
    - 資料不足(sparkline<2 個有效點、增量型無前一筆快照)→ 回 None/空字串,不編造數字補齊。
"""
import datetime as dt


# ----------------------------- 共用小工具 -----------------------------
def _parse_num(v):
    """把 CSV 讀出來的字串/None/int 轉成 int;轉不出來(空字串、None、非數字)一律回 None。"""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


# ----------------------------- 1. 同日多輪去重 -----------------------------
def dedupe_daily_rows(rows, key_cols):
    """同一 (date, *key_cols) 鍵的多筆列(手動重跑造成)只留最後一筆(較晚出現的覆蓋較早的)。

    rows 需為檔案原始順序(append-only,同日重跑的較新一輪必然排在後面)。
    key_cols 例:GitHub=["rank"]、HF=["type","rank"]。
    """
    keyed = {}
    for r in rows:
        key = (r.get("date"),) + tuple(r.get(c) for c in key_cols)
        keyed[key] = r
    return list(keyed.values())


# ----------------------------- 2. 找「昨日」(嚴格更早的最近一天) -----------------------------
def find_prior_date(rows, today):
    """回傳 rows 裡日期嚴格小於 today 的最近一天;沒有則回 None。

    「昨日」定義=資料裡日期嚴格小於今天的最近一天(不是嚴格意義的自然日昨天;
    停跑幾天後重啟,仍會抓到停跑前最後一天的資料,標記照樣正確)。
    """
    prior_dates = {r.get("date") for r in rows if r.get("date") and r.get("date") < today}
    return max(prior_dates) if prior_dates else None


# ----------------------------- 3. 昨日排名比較標記 -----------------------------
def rank_changes(today_names, prior_names):
    """today_names/prior_names:依名次排好的名稱 list(index 0=第一名)。

    回 dict name→("up",n)/("down",n)/("new",None)/("same",None)。
    只針對 today_names 裡的項目(不回報「昨日在、今日已掉出榜外」的項目)。
    """
    prior_rank = {n: i + 1 for i, n in enumerate(prior_names)}
    out = {}
    for i, n in enumerate(today_names):
        today_rank = i + 1
        pr = prior_rank.get(n)
        if pr is None:
            out[n] = ("new", None)
        elif pr == today_rank:
            out[n] = ("same", None)
        elif pr > today_rank:
            out[n] = ("up", pr - today_rank)
        else:
            out[n] = ("down", today_rank - pr)
    return out


def format_rank_change(change):
    """把 rank_changes 的 tuple 轉成顯示字串:↑n/↓n/NEW/—。"""
    kind, n = change
    if kind == "new":
        return "NEW"
    if kind == "same":
        return "—"
    if kind == "up":
        return f"↑{n}"
    if kind == "down":
        return f"↓{n}"
    return "—"


# ----------------------------- 4. 七日迷你趨勢線數列 -----------------------------
def spark_series(rows, name_col, value_col, item_name, end_date, days=7, diff=False):
    """回長度=days 的 list[int|None],對應 end_date 往前推 days-1 天到 end_date(含),缺天=None。

    diff=False(預設;GitHub 的 new_stars、Ollama 的 pulls_delta 皆已是「當日值」,直接讀):
        該日 value_col 直接讀值,缺列/空字串/解析不到 → None。
    diff=True(HF 用,likes/downloads 是累積值,需要自己算增量):
        該日累積值 − 其「上一個有資料日」的累積值;找不到前一筆快照 → None(不編造)。
    """
    end = dt.date.fromisoformat(end_date)
    date_list = [(end - dt.timedelta(days=(days - 1 - i))).isoformat() for i in range(days)]

    by_date = {}
    for r in rows:
        if r.get(name_col) != item_name:
            continue
        d = r.get("date")
        if not d or d > end_date:
            continue
        by_date[d] = _parse_num(r.get(value_col))

    if not diff:
        return [by_date.get(d) for d in date_list]

    sorted_dates = sorted(by_date.keys())
    out = []
    for d in date_list:
        cur = by_date.get(d)
        if cur is None:
            out.append(None)
            continue
        priors = [pd for pd in sorted_dates if pd < d]
        if not priors:
            out.append(None)
            continue
        prev_val = by_date.get(priors[-1])
        out.append((cur - prev_val) if prev_val is not None else None)
    return out


# ----------------------------- 5. 純 Python SVG 迷你長條圖 -----------------------------
def svg_sparkline(series, w=64, h=16):
    """series 內 None=缺口不畫。全 None 或有效點<2 → 回空字串(不畫,誠實;而非畫假的平線)。"""
    valid = [v for v in series if v is not None]
    if len(valid) < 2:
        return ""
    n = len(series)
    bar_w = w / n
    vmax = max(valid)
    vmin = min(0, min(valid))
    rng = (vmax - vmin) or 1
    bars = []
    for i, v in enumerate(series):
        if v is None:
            continue
        bar_h = max(1, round((v - vmin) / rng * (h - 1)))
        x = round(i * bar_w)
        y = h - bar_h
        bw_draw = max(1, round(bar_w) - 1)
        bars.append(f'<rect x="{x}" y="{y}" width="{bw_draw}" height="{bar_h}"/>')
    return f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(bars)}</svg>'


# ----------------------------- 6. 週界/月界 -----------------------------
def period_bounds(period, today):
    """period="week"(ISO 週一起)/"month"(1 號起)。回 (start_date, end_date) 字串,end=today。"""
    d = dt.date.fromisoformat(today)
    if period == "week":
        start = d - dt.timedelta(days=d.weekday())  # weekday()==0 為週一
    elif period == "month":
        start = d.replace(day=1)
    else:
        raise ValueError(f"未知 period:{period}(只接受 'week'/'month')")
    return start.isoformat(), today


def covered_days(rows, period, today):
    """回 (period 起始日字串, 該期間內實際有資料的天數)。給頁面印「統計自…起(N 天)」用,
    N=實際有資料的天數(不是理論天數),資料不足時如實顯示天數少。"""
    start, end = period_bounds(period, today)
    dates = {r.get("date") for r in rows if r.get("date") and start <= r["date"] <= end}
    return start, len(dates)


# ----------------------------- 7. 本週/本月累積榜 -----------------------------
def _sum_leaderboard(rows, name_col, value_col, extra_cols, top):
    """Σ 型:期間內逐日加總(GitHub=Σnew_stars、OpenRouter=Σtotal_tokens)。"""
    agg = {}
    for r in rows:
        name = r.get(name_col)
        if not name:
            continue
        v = _parse_num(r.get(value_col)) or 0
        e = agg.setdefault(name, {"name": name, "value": 0, "days": 0})
        e["value"] += v
        e["days"] += 1
        for k, col in extra_cols.items():
            e[k] = r.get(col)
    out = list(agg.values())
    out.sort(key=lambda x: x["value"], reverse=True)
    return out[:top]


def _delta_leaderboard(rows, name_col, value_cols, extra_cols, top):
    """增量型:期間「末快照 − 首快照」(HF likes/downloads、Ollama pulls)。無法算(只出現一天)→ 該欄 None。"""
    by_name = {}
    for r in rows:
        name = r.get(name_col)
        if not name:
            continue
        by_name.setdefault(name, []).append(r)
    out = []
    for name, rs in by_name.items():
        rs = sorted(rs, key=lambda r: r.get("date") or "")
        first, last = rs[0], rs[-1]
        entry = {"name": name, "days": len(rs)}
        has_two_snapshots = len(rs) >= 2
        for vc in value_cols:
            fv, lv = _parse_num(first.get(vc)), _parse_num(last.get(vc))
            entry[f"{vc}_delta"] = ((lv - fv) if (has_two_snapshots and fv is not None and lv is not None)
                                     else None)
        for k, col in extra_cols.items():
            entry[k] = last.get(col)
        out.append(entry)
    primary = f"{value_cols[0]}_delta"
    out.sort(key=lambda x: (x[primary] if x[primary] is not None else -1), reverse=True)
    return out[:top]


def _max_leaderboard(rows, name_col, value_col, dedupe_col, extra_cols, top):
    """最高值型:期間內出現過的項目按 dedupe_col 去重,取最高值(HN=max score、PH=max votes)。"""
    by_key = {}
    for r in rows:
        key = r.get(dedupe_col) or r.get(name_col)
        if not key:
            continue
        v = _parse_num(r.get(value_col))
        cur = by_key.get(key)
        if cur is None or (v is not None and (cur["value"] is None or v > cur["value"])):
            entry = {"name": r.get(name_col), "value": v}
            for k, col in extra_cols.items():
                entry[k] = r.get(col)
            by_key[key] = entry
    out = list(by_key.values())
    out.sort(key=lambda x: (x["value"] if x["value"] is not None else -1), reverse=True)
    return out[:top]


_HF_TYPE_MAP = {"hf_model": "模型", "hf_dataset": "資料集", "hf_space": "Spaces"}


def period_leaderboard(rows, period, today, source, top=10):
    """rows:某一源已 dedupe 過的全部歷史列(list[dict],DictReader 讀出的 CSV)。

    period:"week"/"month"。today:'YYYY-MM-DD'。
    source:"github" / "openrouter" / "hf_model" / "hf_dataset" / "hf_space" / "ollama" / "hn" / "ph"。
    回排序後 list[dict](已截斷 top)。誠實界線:僅統計期間內「進過每日榜」的項目(rows 只有日榜資料,
    天生就是這個限制;本函式不額外過濾,如實反映資料庫本來的樣子)。
    """
    start, end = period_bounds(period, today)
    in_range = [r for r in rows if r.get("date") and start <= r["date"] <= end]

    if source == "github":
        return _sum_leaderboard(in_range, "repo", "new_stars",
                                 {"url": "url", "desc": "description", "lang": "language"}, top)
    if source == "openrouter":
        return _sum_leaderboard(in_range, "model", "total_tokens", {}, top)
    if source in _HF_TYPE_MAP:
        typed = [r for r in in_range if r.get("type") == _HF_TYPE_MAP[source]]
        return _delta_leaderboard(typed, "id", ["likes", "downloads"], {"url": "url"}, top)
    if source == "ollama":
        return _delta_leaderboard(in_range, "model", ["pulls"], {"url": "url", "desc": "desc"}, top)
    if source == "hn":
        return _max_leaderboard(in_range, "title", "points", "url",
                                 {"url": "url", "hn_url": "hn_url"}, top)
    if source == "ph":
        return _max_leaderboard(in_range, "name", "votes", "url",
                                 {"url": "url", "tagline": "tagline"}, top)
    raise ValueError(f"未知 source:{source}")
