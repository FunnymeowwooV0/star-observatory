"""
解析器單元測試(離線,用 tests/fixtures/ 的真實快照)。

守的是本專案最大風險:GitHub 沒官方 API、靠解析 HTML,版面一改就會無聲壞掉。
這些測試把「解析出 10 筆、按新增星遞減、欄位正確」釘死,選擇器被改壞會立刻紅燈。

跑法:  python -m unittest discover -s tests -v
"""
import json
import os
import re
import sys
import unittest
import unittest.mock
from xml.sax import saxutils

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_trends as ft  # noqa: E402
import sources_extra as se  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class TestGithubParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "github_trending_daily.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_returns_exactly_top_n(self):
        repos = ft.parse_github_trending(self.html, top=10)
        self.assertEqual(len(repos), 10)

    def test_all_have_period_stars(self):
        repos = ft.parse_github_trending(self.html, top=10)
        for r in repos:
            self.assertIsInstance(r["period_stars"], int,
                                  f"{r['name']} 抓不到新增星(float-sm-right 選擇器可能壞了)")

    def test_sorted_by_new_stars_desc(self):
        repos = ft.parse_github_trending(self.html, top=10)
        stars = [r["period_stars"] for r in repos]
        self.assertEqual(stars, sorted(stars, reverse=True), "排序不是新增星由多到少")
        self.assertEqual(stars[0], max(stars), "第一名不是新增星最多者")

    def test_name_and_url_shape(self):
        repos = ft.parse_github_trending(self.html, top=10)
        for r in repos:
            self.assertRegex(r["name"], r"^[^/\s]+/[^/\s]+$", f"repo 名稱格式不對:{r['name']}")
            self.assertTrue(r["url"].startswith("https://github.com/"), r["url"])

    def test_empty_html_raises(self):
        # 版面大改 / 抓到空頁 → 必須丟例外,不能靜默回空榜
        with self.assertRaises(RuntimeError):
            ft.parse_github_trending("<html><body>no repos here</body></html>")


class TestHfParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "hf_trending_model.json"), encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_returns_top_n(self):
        items = ft.parse_hf_trending(self.data, "model", top=5)
        self.assertEqual(len(items), 5)

    def test_default_daily_limit_is_ten(self):
        data = {
            "recentlyTrending": [
                {"repoData": {"id": f"org/model-{i}", "likes": i, "downloads": i * 10}}
                for i in range(12)
            ]
        }

        self.assertEqual(ft.HF_TOP, 10)
        self.assertEqual(len(ft.parse_hf_trending(data, "model")), 10)

    def test_fields_and_url(self):
        items = ft.parse_hf_trending(self.data, "model", top=5)
        for it in items:
            self.assertIn("/", it["id"], f"HF id 應為 author/name:{it['id']}")
            self.assertEqual(it["url"], f"https://huggingface.co/{it['id']}")
            self.assertTrue(it["likes"] is None or isinstance(it["likes"], int))

    def test_dataset_url_prefix(self):
        items = ft.parse_hf_trending(self.data, "dataset", top=1)
        self.assertTrue(items[0]["url"].startswith("https://huggingface.co/datasets/"))

    def test_space_url_prefix(self):
        items = ft.parse_hf_trending(self.data, "space", top=1)
        self.assertTrue(items[0]["url"].startswith("https://huggingface.co/spaces/"))

    def test_missing_key_returns_empty(self):
        self.assertEqual(ft.parse_hf_trending({}, "model"), [])


class TestHnParse(unittest.TestCase):
    """HN Algolia 頭版解析(真實 API 快照,截短存 fixtures/hn_frontpage.json)。"""

    @classmethod
    def setUpClass(cls):
        cls.data = _load("hn_frontpage.json")

    def test_returns_at_most_top_n(self):
        items = se.parse_hn_frontpage(self.data, top=10)
        self.assertLessEqual(len(items), 10)
        self.assertEqual(len(items), 10)  # fixture 有 12 筆 → 取上限 10

    def test_sorted_by_points_desc(self):
        items = se.parse_hn_frontpage(self.data, top=10)
        pts = [it["points"] for it in items]
        self.assertEqual(pts, sorted(pts, reverse=True), "HN 沒有按 points 由多到少排序")
        self.assertEqual(pts[0], max(pts), "第一名不是分數最高者")

    def test_field_shape(self):
        items = se.parse_hn_frontpage(self.data, top=10)
        for it in items:
            self.assertIsInstance(it["points"], int)
            self.assertIsInstance(it["title"], str)
            self.assertTrue(it["hn_url"].startswith("https://news.ycombinator.com/item?id="), it["hn_url"])
            self.assertTrue(it["url"], "外部 url 為空時應退回討論頁連結,不得為空")

    def test_null_external_url_falls_back_to_discussion(self):
        # fixture 中有一筆外部 url=null → url 應等於其 HN 討論頁連結
        items = se.parse_hn_frontpage(self.data, top=12)
        fell_back = [it for it in items if it["url"] == it["hn_url"]]
        self.assertTrue(fell_back, "外部 url 為 null 時未退回討論頁連結")

    def test_empty_input_returns_empty(self):
        self.assertEqual(se.parse_hn_frontpage({}), [])
        self.assertEqual(se.parse_hn_frontpage({"hits": []}), [])


class TestOpenRouterParse(unittest.TestCase):
    """OpenRouter 內部 rankings 解析(真實 API 快照,截短存 fixtures/openrouter_rankings.json)。

    脆弱源:解析不到必須丟例外,不得靜默回空榜。
    """

    @classmethod
    def setUpClass(cls):
        cls.data = _load("openrouter_rankings.json")

    def test_returns_at_most_top_n(self):
        items = se.parse_openrouter_rankings(self.data, top=10)
        self.assertLessEqual(len(items), 10)
        self.assertTrue(items)

    def test_sorted_by_total_tokens_desc(self):
        items = se.parse_openrouter_rankings(self.data, top=10)
        tot = [it["total_tokens"] for it in items]
        self.assertEqual(tot, sorted(tot, reverse=True), "OpenRouter 沒有按 total_tokens 由多到少排序")

    def test_field_shape_and_sum(self):
        items = se.parse_openrouter_rankings(self.data, top=10)
        for it in items:
            self.assertIn("model", it)
            self.assertEqual(it["total_tokens"], it["prompt_tokens"] + it["completion_tokens"],
                             "total_tokens 應為 prompt+completion 之和")

    def test_only_latest_day_counted(self):
        # fixture 摻了一筆較舊日期、prompt_tokens=999...(超大)的 row,必須被排除
        items = se.parse_openrouter_rankings(self.data, top=10)
        self.assertTrue(all(it["prompt_tokens"] < 999999999999999 for it in items),
                        "舊日期的 row 不該被計入最新一日榜")

    def test_variants_aggregated_per_model(self):
        # fixture 為第一個 model 加了一筆同日 variant row(prompt=1000, completion=2000)
        # 聚合後該 model 的 tokens 應包含這筆加總
        items = se.parse_openrouter_rankings(self.data, top=10)
        models = [it["model"] for it in items]
        self.assertEqual(len(models), len(set(models)), "同一 model 的多個 variant 應被合併成一列")

    def test_no_data_raises(self):
        with self.assertRaises(RuntimeError):
            se.parse_openrouter_rankings({})
        with self.assertRaises(RuntimeError):
            se.parse_openrouter_rankings({"data": []})

    def test_target_date_picks_that_day_not_latest(self):
        # fixture 含 2026-07-20(僅一筆,含超大 prompt_tokens 的 row)與 2026-07-21(多筆)兩天;
        # 預設(無 target_date)取最新一天 07-21,帶 target_date='2026-07-20' 應改取 07-20 那天。
        default_items = se.parse_openrouter_rankings(self.data, top=10)
        older_items = se.parse_openrouter_rankings(self.data, top=10, target_date="2026-07-20")
        self.assertTrue(older_items)
        self.assertNotEqual(
            {it["model"] for it in default_items}, {it["model"] for it in older_items},
            "target_date 應改取指定那天的資料,結果不該跟預設(最新一天)完全相同"
        )
        self.assertTrue(any(it["prompt_tokens"] >= 999999999999999 for it in older_items),
                        "target_date='2026-07-20' 應該取到該日的 row(fixture 裡那天只有這筆髒資料)")


class TestPhParse(unittest.TestCase):
    """Product Hunt GraphQL 解析(synthetic fixture,見 fixtures/ph_posts.json 內註)。"""

    @classmethod
    def setUpClass(cls):
        cls.data = _load("ph_posts.json")

    def test_returns_at_most_top_n(self):
        items = se.parse_ph_posts(self.data, top=3)
        self.assertEqual(len(items), 3)

    def test_sorted_by_votes_desc(self):
        items = se.parse_ph_posts(self.data, top=10)
        votes = [it["votes"] for it in items]
        self.assertEqual(votes, sorted(votes, reverse=True), "PH 沒有按票數由多到少排序")

    def test_field_shape(self):
        items = se.parse_ph_posts(self.data, top=10)
        for it in items:
            self.assertIsInstance(it["name"], str)
            self.assertIsInstance(it["tagline"], str)
            self.assertIsInstance(it["description"], str)
            self.assertTrue(it["description"], f"{it['name']} 缺 Product Hunt description")
            self.assertIsInstance(it["votes"], int)
            self.assertTrue(it["url"].startswith("https://"), it["url"])

    def test_description_is_preserved_verbatim(self):
        items = {it["name"]: it for it in se.parse_ph_posts(self.data, top=10)}
        self.assertEqual(
            items["Zeta Copilot"]["description"],
            "A terminal assistant that explains commands, drafts shell workflows, and keeps "
            "generated operations visible before the user runs them.",
        )

    def test_fetch_query_requests_description_in_existing_post_query(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = self.data
        with unittest.mock.patch.object(se.requests, "post", return_value=response) as post:
            se.fetch_ph_posts("fixture-token", posted_after="2026-08-08T01:00:00Z")
        query = post.call_args.kwargs["json"]["query"]
        self.assertIn("description", query)

    def test_empty_input_returns_empty(self):
        self.assertEqual(se.parse_ph_posts({}), [])
        self.assertEqual(se.parse_ph_posts({"data": {"posts": {"edges": []}}}), [])


class TestOllamaParse(unittest.TestCase):
    """Ollama 模型庫 popular 榜解析(合成 fixture,見 fixtures/ollama_library.html)。

    脆弱源(爬 HTML):解析不到必須丟例外。榜序=頁面原序,不按 Pulls 重排。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "ollama_library.html"), encoding="utf-8") as f:
            cls.html = f.read()

    # 頁面/榜序(fixture 刻意讓頁序≠Pulls 遞減,用來抓「有沒有偷偷重排」)
    PAGE_ORDER = ["alpha-model", "beta-model", "gamma-embed", "delta-model", "epsilon-model",
                  "zeta-model", "eta-model", "theta-model", "iota-model", "kappa-model"]

    def test_returns_top_10_in_page_order(self):
        items = se.parse_ollama_library(self.html, top=10)
        self.assertEqual(len(items), 10)
        self.assertEqual([it["name"] for it in items], self.PAGE_ORDER,
                         "Ollama 榜序必須照頁面 popular 原序,不得取前 10 之外或改序")

    def test_not_resorted_by_pulls(self):
        items = se.parse_ollama_library(self.html, top=10)
        names = [it["name"] for it in items]
        by_pulls = [it["name"] for it in sorted(items, key=lambda x: x["pulls"], reverse=True)]
        self.assertNotEqual(names, by_pulls,
                            "頁序與 Pulls 遞減序在此 fixture 應不同;相同代表被錯誤重排了")

    def test_pulls_abbreviation_parsing(self):
        items = {it["name"]: it for it in se.parse_ollama_library(self.html, top=10)}
        self.assertEqual(items["alpha-model"]["pulls"], 1_200_000_000)   # 1.2B
        self.assertEqual(items["kappa-model"]["pulls"], 7_000_000_000)   # 7B
        self.assertEqual(items["gamma-embed"]["pulls"], 79_300_000)      # 79.3M 小數
        self.assertEqual(items["theta-model"]["pulls"], 3_140_000)       # 3.14M 兩位小數
        self.assertEqual(items["beta-model"]["pulls"], 500_000)          # 500K
        self.assertEqual(items["eta-model"]["pulls"], 1_500)             # 1.5K
        self.assertEqual(items["epsilon-model"]["pulls"], 900)           # 無縮寫

    def test_field_shape(self):
        for it in se.parse_ollama_library(self.html, top=10):
            self.assertIsInstance(it["name"], str)
            self.assertEqual(it["url"], f"https://ollama.com/library/{it['name']}")
            self.assertIsInstance(it["desc"], str)
            self.assertTrue(it["desc"], f"{it['name']} 缺官方描述")
            self.assertIsInstance(it["pulls"], int)
            self.assertIsInstance(it["caps"], list)
            self.assertIsInstance(it["updated"], str)

    def test_caps_excludes_sizes_and_allows_empty(self):
        items = {it["name"]: it for it in se.parse_ollama_library(self.html, top=10)}
        self.assertEqual(items["alpha-model"]["caps"], ["tools"])
        self.assertEqual(items["delta-model"]["caps"], ["thinking", "vision"])
        self.assertEqual(items["beta-model"]["caps"], [], "無能力標籤應為空 list,不得混入尺寸(1b/3b)")
        # 尺寸標籤(含數字)絕不能出現在 caps
        for it in items.values():
            for c in it["caps"]:
                self.assertFalse(any(ch.isdigit() for ch in c),
                                 f"caps 不該含尺寸標籤:{c}")

    def test_empty_or_broken_html_raises(self):
        with self.assertRaises(RuntimeError):
            se.parse_ollama_library("<html><body>no models here</body></html>")
        with self.assertRaises(RuntimeError):
            se.parse_ollama_library("")


class TestOllamaDelta(unittest.TestCase):
    """compute_pull_deltas 純函式:今日 pulls − 最近先前日快照 = 今日新增下載。"""

    TODAY = "2026-07-22"
    TODAY_ITEMS = [
        {"name": "a", "pulls": 1000},
        {"name": "b", "pulls": 500},
        {"name": "c", "pulls": 300},  # 新進榜,先前無快照
    ]

    def test_with_prior_snapshot(self):
        rows = [
            {"date": "2026-07-21", "model": "a", "pulls": "800"},
            {"date": "2026-07-21", "model": "b", "pulls": "500"},
            {"date": "2026-07-20", "model": "a", "pulls": "700"},  # 更舊,不該被當基準
        ]
        d = se.compute_pull_deltas(self.TODAY_ITEMS, rows, self.TODAY)
        self.assertEqual(d["a"], 200)   # 1000 − 800(取最近先前日 07-21)
        self.assertEqual(d["b"], 0)     # 500 − 500
        self.assertIsNone(d["c"])       # 先前無此模型 → 不編造

    def test_no_prior_snapshot_all_none(self):
        d = se.compute_pull_deltas(self.TODAY_ITEMS, [], self.TODAY)
        self.assertEqual(d, {"a": None, "b": None, "c": None})

    def test_same_day_rerun_does_not_use_today_as_base(self):
        # 今天已經跑過一次(csv 有 today 的列)+ 一筆昨天列。
        # today 的列絕不能被當基準(否則 a 會誤算成 1000−1000=0)。
        rows = [
            {"date": self.TODAY, "model": "a", "pulls": "1000"},      # 今天,不得當基準
            {"date": "2026-07-21", "model": "a", "pulls": "800"},     # 昨天,正解基準
        ]
        d = se.compute_pull_deltas(self.TODAY_ITEMS, rows, self.TODAY)
        self.assertEqual(d["a"], 200, "同日重跑不得把今天的列當先前快照")

    def test_same_day_only_no_earlier_returns_none(self):
        # 只有今天的列、無更早日期 → 沒有先前快照 → None(不得 1000−1000=0)
        rows = [{"date": self.TODAY, "model": "a", "pulls": "1000"}]
        d = se.compute_pull_deltas(self.TODAY_ITEMS, rows, self.TODAY)
        self.assertIsNone(d["a"], "無嚴格更早的日期時 delta 應為 None")


class TestRedditParse(unittest.TestCase):
    """Reddit r/LocalLLaMA 週榜解析(真實 old.reddit 頁面快照+一筆合成的隱藏分數列,
    見 fixtures/reddit_localllama_weekly.html)。

    脆弱源(爬 HTML):解析不到必須丟例外。榜序=頁面原序(官方 top/week 排序)。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "reddit_localllama_weekly.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_returns_at_most_top_n(self):
        items = se.parse_reddit_top(self.html, top=10)
        self.assertEqual(len(items), 9)  # fixture 共 9 筆(8 真實+1 合成)

    def test_capped_by_top(self):
        items = se.parse_reddit_top(self.html, top=3)
        self.assertEqual(len(items), 3)

    def test_field_shape(self):
        items = se.parse_reddit_top(self.html, top=10)
        for it in items:
            self.assertIsInstance(it["title"], str)
            self.assertTrue(it["title"], "標題不得為空")
            self.assertTrue(it["score"] is None or isinstance(it["score"], int))
            self.assertTrue(it["comments"] is None or isinstance(it["comments"], int))
            self.assertTrue(it["permalink"].startswith("https://www.reddit.com/r/LocalLLaMA/"),
                             f"permalink 未正規化成 www.reddit.com:{it['permalink']}")
            self.assertEqual(it["body"], "", "HTML 週榜不新爬內文，只有 feed 現有正文可用")

    def test_self_post_has_no_external_url(self):
        items = {it["title"]: it for it in se.parse_reddit_top(self.html, top=10)}
        self_post = items["Anthropic and OpenAI don't have secret sauce"]
        self.assertIsNone(self_post["external_url"], "自帖不該有 external_url")

    def test_link_post_has_external_url(self):
        items = {it["title"]: it for it in se.parse_reddit_top(self.html, top=10)}
        link_post = items["Linus Torvalds tells people to stop attacking others for using AI"]
        self.assertEqual(link_post["external_url"], "https://www.phoronix.com/news/Linux-Is-Not-Anti-AI")

    def test_hidden_score_and_missing_comments_become_none(self):
        # fixture 摻了一筆合成貼文,data-score="•"(隱藏分數)、data-comments-count=""
        items = {it["title"]: it for it in se.parse_reddit_top(self.html, top=10)}
        synth = items["Synthetic post with hidden score (測試 None 欄位)"]
        self.assertIsNone(synth["score"], "隱藏分數(•)應解析為 None")
        self.assertIsNone(synth["comments"], "缺留言數應解析為 None")

    def test_permalink_normalized_shape(self):
        items = se.parse_reddit_top(self.html, top=10)
        for it in items:
            self.assertRegex(it["permalink"], r"^https://www\.reddit\.com/r/LocalLLaMA/comments/",
                             f"permalink 格式不對:{it['permalink']}")

    def test_empty_or_broken_html_raises(self):
        with self.assertRaises(RuntimeError):
            se.parse_reddit_top("<html><body>no posts here</body></html>")
        with self.assertRaises(RuntimeError):
            se.parse_reddit_top("")


class TestRedditRssParse(unittest.TestCase):
    """Reddit r/LocalLLaMA 週榜 **RSS(Atom)** 解析(2026-08-06 起雲端改走這條路)。

    fixture=真實 old.reddit `.rss?t=week` 快照的前 12 則 + 1 則合成列(標題含 HTML entity、
    外部連結帶 query),見 fixtures/reddit_localllama_weekly.rss。
    敵意內容(作者偽造頁腳)另見 TestRedditRssHostileContent;
    標題解碼層數另見 TestRedditRssTitleDecoding。
    口徑:RSS **不提供分數與留言數** → score/comments 一律 None,不得編造或填 0。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, "reddit_localllama_weekly.rss"), encoding="utf-8") as f:
            cls.xml = f.read()

    def test_default_top_is_ten_even_though_feed_has_more(self):
        self.assertEqual(len(se.parse_reddit_rss(self.xml)), 10)

    def test_capped_by_top_and_full_feed_reachable(self):
        self.assertEqual(len(se.parse_reddit_rss(self.xml, top=3)), 3)
        self.assertEqual(len(se.parse_reddit_rss(self.xml, top=25)), 13)

    def test_order_follows_feed_order(self):
        items = se.parse_reddit_rss(self.xml, top=3)
        self.assertEqual(items[0]["title"], "Qwen3.8-27B announced alongside Qwen3.8-Max")
        self.assertEqual(
            items[1]["title"],
            "Daniel Han of Unsloth validates Qwen3.8-27B will run only 17GB VRAM")

    def test_score_and_comments_are_always_none(self):
        for it in se.parse_reddit_rss(self.xml, top=25):
            self.assertIsNone(it["score"], "RSS 沒有分數,不得編造")
            self.assertIsNone(it["comments"], "RSS 沒有留言數,不得編造")

    def test_permalink_normalized_to_www(self):
        for it in se.parse_reddit_rss(self.xml, top=25):
            self.assertRegex(it["permalink"], r"^https://www\.reddit\.com/r/LocalLLaMA/comments/",
                             f"permalink 未正規化成 www.reddit.com:{it['permalink']}")

    def test_self_post_has_no_external_url(self):
        items = {it["title"]: it for it in se.parse_reddit_rss(self.xml, top=25)}
        # 自貼文:RSS 的 [link] 與 [comments] 指向同一個 permalink
        self.assertIsNone(items["Qwen3.8-27B announced alongside Qwen3.8-Max"]["external_url"])
        self.assertIsNone(items[
            "I CANNOT believe I've got DeepSeek-V4-Flash-0731, a frontier model, "
            "running on my home PC. Insane!"]["external_url"])

    def test_link_post_keeps_external_url(self):
        items = {it["title"]: it for it in se.parse_reddit_rss(self.xml, top=25)}
        self.assertEqual(
            items["Daniel Han of Unsloth validates Qwen3.8-27B will run only 17GB VRAM"]["external_url"],
            "https://i.redd.it/kabmtuygn3hh1.jpeg")
        self.assertEqual(
            items["Hugging Face CEO says China is winning the AI race and dominating on open models"]["external_url"],
            "https://www.cnbc.com/2026/08/03/hugging-face-china-ai-race-open-models.html")

    def test_title_html_entities_unescaped(self):
        # 只解一層(XML parser 那層)。fixture 過去把合成列的標題寫成雙重編碼,
        # 等於把「解兩次」釘死成規格;2026-08-07 改回單層編碼,見 TestRedditRssTitleDecoding。
        titles = [it["title"] for it in se.parse_reddit_rss(self.xml, top=25)]
        self.assertIn("Fixture & entity row 'quoted'", titles)
        for t in titles:
            self.assertNotIn("&amp;", t)
            self.assertNotIn("&#39;", t)

    def test_external_url_query_string_preserved(self):
        items = {it["title"]: it for it in se.parse_reddit_rss(self.xml, top=25)}
        self.assertEqual(items["Fixture & entity row 'quoted'"]["external_url"],
                         "https://example.com/a?x=1&y=2")

    def test_existing_feed_body_is_clean_plain_text(self):
        items = {it["title"]: it for it in se.parse_reddit_rss(self.xml, top=25)}
        body = items["Kimi K3 full model running on 16x GB10 cluster at 20+tps"]["body"]
        self.assertIn("38tps peak, 750tps prefill", body)
        self.assertIn("publish the vllm image and instructions", body)
        self.assertNotIn("<p>", body)
        self.assertNotIn("submitted by", body)

    def test_body_is_capped_as_light_material(self):
        author_html = '<div class="md"><p>' + ("useful context " * 300) + "</p></div>"
        content = ("<table><tr><td>" + author_html
                   + _reddit_footer(
                       "https://old.reddit.com/r/LocalLLaMA/comments/1cap001/capped/",
                       "https://old.reddit.com/r/LocalLLaMA/comments/1cap001/capped/")
                   + "</td></tr></table>")
        item = se.parse_reddit_rss(_atom_feed(_atom_entry(
            content,
            "https://old.reddit.com/r/LocalLLaMA/comments/1cap001/capped/",
            title="Capped body")), top=1)[0]
        self.assertLessEqual(len(item["body"]), 1500)
        self.assertTrue(item["body"].endswith("…"))

    def test_field_shape_matches_html_parser_schema(self):
        for it in se.parse_reddit_rss(self.xml, top=25):
            self.assertEqual(
                set(it), {"title", "score", "comments", "permalink", "external_url", "body"})
            self.assertTrue(it["title"], "標題不得為空")

    def test_empty_or_broken_feed_raises(self):
        empty_feed = ('<?xml version="1.0" encoding="UTF-8"?>'
                      '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>')
        with self.assertRaises(RuntimeError):
            se.parse_reddit_rss(empty_feed)
        with self.assertRaises(RuntimeError):
            se.parse_reddit_rss("")
        with self.assertRaises(RuntimeError):
            se.parse_reddit_rss("<html><body>Blocked</body></html>")


class TestFetchRedditUserAgents(unittest.TestCase):
    """fetch_reddit_top_rss()／fetch_reddit_top() 送出的 User-Agent(離線,mock requests.get,零連網)。

    背景:2026-08-06 Actions 探針證實 old.reddit `.rss` 端點在機房 IP 對誠實 UA
    (`star-observatory/1.0 (personal trend dashboard)`)回 200;但正式程式碼原本共用
    `UA`(冒充瀏覽器)從未在該端點被驗證過。RSS 路徑改用經驗證的字串,HTML 路徑
    (本機、住宅 IP)維持共用 UA 不動。
    """

    def test_fetch_reddit_top_rss_sends_verified_ua(self):
        with unittest.mock.patch.object(se, "requests") as mock_requests:
            mock_resp = unittest.mock.Mock()
            mock_resp.text = open(
                os.path.join(FIX, "reddit_localllama_weekly.rss"), encoding="utf-8"
            ).read()
            mock_resp.raise_for_status = lambda: None
            mock_requests.get.return_value = mock_resp

            se.fetch_reddit_top_rss()

            _, kwargs = mock_requests.get.call_args
            self.assertEqual(kwargs["headers"]["User-Agent"], se.REDDIT_RSS_UA)

    def test_reddit_rss_ua_does_not_impersonate_a_browser(self):
        self.assertNotIn("Mozilla", se.REDDIT_RSS_UA)

    def test_fetch_reddit_top_html_still_sends_shared_ua(self):
        with unittest.mock.patch.object(se, "requests") as mock_requests:
            mock_resp = unittest.mock.Mock()
            mock_resp.text = open(
                os.path.join(FIX, "reddit_localllama_weekly.html"), encoding="utf-8"
            ).read()
            mock_resp.raise_for_status = lambda: None
            mock_requests.get.return_value = mock_resp

            se.fetch_reddit_top()

            _, kwargs = mock_requests.get.call_args
            self.assertEqual(kwargs["headers"]["User-Agent"], se.UA)


def _atom_feed(*entries):
    """把 entry 片段包成一份最小 Atom feed。"""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            '<title>top scoring links : LocalLLaMA</title>'
            + "".join(entries) + '</feed>')


def _atom_entry(content_html, link_href, title="Hostile post", raw_title=None):
    """組一則 entry。content 依 Atom `type="html"` 規定整段跳脫(貼文作者只能碰這裡);
    link 是 feed 產生器自己輸出的兄弟層級元素(作者碰不到)。

    raw_title 提供時直接當作 XML 內文寫入(用來測「作者字面打 entity」的情境)。
    """
    return ("<entry>"
            f'<content type="html">{saxutils.escape(content_html)}</content>'
            "<id>t3_atk001</id>"
            f"<link href={saxutils.quoteattr(link_href)} />"
            f"<title>{raw_title if raw_title is not None else saxutils.escape(title)}</title>"
            "</entry>")


# 真實 Reddit 頁腳:由 feed 產生器附加在作者內文**之後**。
def _reddit_footer(link_href, comments_href):
    return (' &#32; submitted by &#32; <a href="https://old.reddit.com/user/attacker">'
            " /u/attacker </a> <br/> "
            f'<span><a href="{link_href}">[link]</a></span> &#32; '
            f'<span><a href="{comments_href}">[comments]</a></span>')


class TestRedditRssHostileContent(unittest.TestCase):
    """貼文作者只能控制 `<content>`;不得讓他劫持公開頁面上的連結。

    攻擊面:Reddit 把作者 markdown 轉成的 HTML 放在 `<content>` **前段**,官方頁腳
    `[link]` / `[comments]` 附在**後段**。作者寫 markdown `[[comments]](http://…)`
    就會產生一個 label 完全相同、且排在官方頁腳之前的 `<a>`。
    站是公開的 GitHub Pages,主連結被換掉=讀者以為點的是討論串,實際被導去攻擊者的站,
    而且惡意 URL 會被寫進 data/reddit_weekly.csv 永久留存。
    """

    HOSTILE_THREAD = ("https://old.reddit.com/r/LocalLLaMA/comments/1atk001/"
                      "hostile_post/")

    def _hostile_feed(self):
        author_html = (
            '<!-- SC_OFF --><div class="md"><p>Great model! '
            '<a href="https://evil.example/pwn">[comments]</a> '
            '<a href="https://evil.example/drive-by">[link]</a></p></div><!-- SC_ON -->')
        content = ("<table> <tr><td> " + author_html
                   + _reddit_footer("https://i.redd.it/legit.jpeg", self.HOSTILE_THREAD)
                   + " </td></tr></table>")
        return _atom_feed(_atom_entry(content, self.HOSTILE_THREAD, title="Hostile post"))

    def test_author_cannot_hijack_permalink(self):
        item = se.parse_reddit_rss(self._hostile_feed(), top=1)[0]
        self.assertNotIn("evil.example", item["permalink"],
                         f"permalink 被貼文作者劫持:{item['permalink']}")
        self.assertEqual(
            item["permalink"],
            "https://www.reddit.com/r/LocalLLaMA/comments/1atk001/hostile_post/")

    def test_author_cannot_hijack_external_url(self):
        item = se.parse_reddit_rss(self._hostile_feed(), top=1)[0]
        self.assertNotIn("evil.example", str(item["external_url"]),
                         f"external_url 被貼文作者劫持:{item['external_url']}")
        self.assertEqual(item["external_url"], "https://i.redd.it/legit.jpeg")

    def test_author_body_is_data_only_and_does_not_include_feed_footer(self):
        item = se.parse_reddit_rss(self._hostile_feed(), top=1)[0]
        self.assertIn("Great model!", item["body"])
        self.assertNotIn("<a ", item["body"])
        self.assertNotIn("submitted by", item["body"])

    def test_non_reddit_entry_link_is_dropped_not_trusted(self):
        """entry 層 `<link href>` 不是討論串網址 → fail-closed,該則跳過(不放行可疑值)。"""
        content = ("<table> <tr><td> <div class=\"md\"><p>x</p></div> "
                   + _reddit_footer("https://i.redd.it/legit.jpeg",
                                    "https://evil.example/pwn")
                   + " </td></tr></table>")
        good = _atom_entry(
            "<table> <tr><td> <div class=\"md\"><p>ok</p></div> "
            + _reddit_footer("https://example.com/story",
                             "https://old.reddit.com/r/LocalLLaMA/comments/1ok0001/ok/")
            + " </td></tr></table>",
            "https://old.reddit.com/r/LocalLLaMA/comments/1ok0001/ok/", title="Good post")
        items = se.parse_reddit_rss(
            _atom_feed(_atom_entry(content, "https://evil.example/pwn"), good), top=10)
        self.assertEqual([it["title"] for it in items], ["Good post"])
        for it in items:
            self.assertNotIn("evil.example", it["permalink"])

    def test_all_entries_non_conforming_raises(self):
        """整批都不合格(=feed 改版或被掉包)→ 丟 RuntimeError,不得靜默回空榜。"""
        bad = _atom_entry("<table></table>", "https://evil.example/pwn")
        with self.assertRaises(RuntimeError):
            se.parse_reddit_rss(_atom_feed(bad, bad))

    def test_entry_link_must_be_https(self):
        bad = _atom_entry(
            "<table></table>",
            "http://old.reddit.com/r/LocalLLaMA/comments/1atk001/hostile_post/")
        with self.assertRaises(RuntimeError):
            se.parse_reddit_rss(_atom_feed(bad))

    def test_lookalike_host_entry_link_is_dropped(self):
        for host in ("https://old.reddit.com.evil.example",
                     "https://www.reddit.com" + "@evil.example",
                     "https://notold.reddit.com",
                     "https://evil.example/old.reddit.com"):
            bad = _atom_entry("<table></table>",
                              host + "/r/LocalLLaMA/comments/1atk001/hostile_post/")
            with self.subTest(host=host):
                with self.assertRaises(RuntimeError):
                    se.parse_reddit_rss(_atom_feed(bad))


class TestRedditWwwNormalisation(unittest.TestCase):
    """`_reddit_www()` 的網域邊界:只換「真的是 reddit 網域」的前綴。"""

    def test_normalises_real_reddit_hosts(self):
        for src in ("https://old.reddit.com/r/x/comments/y/",
                    "https://np.reddit.com/r/x/comments/y/",
                    "https://www.reddit.com/r/x/comments/y/",
                    "http://old.reddit.com/r/x/comments/y/"):
            with self.subTest(src=src):
                self.assertEqual(se._reddit_www(src),
                                 "https://www.reddit.com/r/x/comments/y/")

    def test_uppercase_host_is_normalised(self):
        """DNS 網域不分大小寫,`OLD.reddit.com` 仍是同一台主機,不該漏掉。"""
        self.assertEqual(se._reddit_www("https://OLD.Reddit.COM/r/x/comments/y/"),
                         "https://www.reddit.com/r/x/comments/y/")

    def test_lookalike_domain_is_not_rewritten(self):
        """`old.reddit.com.evil.example` 不是 reddit;改寫會讓它看起來更像真的。"""
        for src in ("https://old.reddit.com.evil.example/r/x/",
                    "https://np.reddit.commercial.example/r/x/",
                    "https://www.reddit.com" + "@evil.example/r/x/"):
            with self.subTest(src=src):
                self.assertEqual(se._reddit_www(src), src)

    def test_non_reddit_url_untouched(self):
        self.assertEqual(se._reddit_www("https://example.com/a?x=1&y=2"),
                         "https://example.com/a?x=1&y=2")
        self.assertEqual(se._reddit_www(""), "")
        self.assertEqual(se._reddit_www(None), "")


class TestRedditRssTitleDecoding(unittest.TestCase):
    """標題只做一層解碼:XML parser 已經解過,再 `html.unescape()` 就是解兩次。"""

    def _title(self, raw_title):
        feed = _atom_feed(_atom_entry(
            "<table> <tr><td> "
            + _reddit_footer("https://example.com/story",
                             "https://old.reddit.com/r/LocalLLaMA/comments/1t0001/t/")
            + " </td></tr></table>",
            "https://old.reddit.com/r/LocalLLaMA/comments/1t0001/t/",
            raw_title=raw_title))
        return se.parse_reddit_rss(feed, top=1)[0]["title"]

    def test_single_layer_decode_only(self):
        """作者標題字面打 `&amp;` → XML 裡是 `&amp;amp;` → 只該解成 `&amp;`。

        解兩次會變成 `&`,與 HTML parser(BeautifulSoup get_text,單層)不一致。
        """
        self.assertEqual(self._title("Tom &amp;amp; Jerry"), "Tom &amp; Jerry")

    def test_normal_entities_still_decoded_once(self):
        self.assertEqual(self._title("Tom &amp; Jerry"), "Tom & Jerry")
        self.assertEqual(self._title("say &quot;hi&quot; &#39;ok&#39;"), 'say "hi" \'ok\'')

    def test_matches_html_parser_behaviour(self):
        """同一則貼文,兩個 parser 的標題必須逐字相同(顯示口徑一致)。"""
        html_page = (
            '<div class="thing" data-permalink="/r/LocalLLaMA/comments/1t0001/t/" '
            'data-score="1" data-comments-count="1" '
            'data-url="/r/LocalLLaMA/comments/1t0001/t/">'
            '<a class="title" href="#">Tom &amp;amp; Jerry</a></div>')
        self.assertEqual(self._title("Tom &amp;amp; Jerry"),
                         se.parse_reddit_top(html_page)[0]["title"])


class TestRedditSnapshotDue(unittest.TestCase):
    """雲端每日管線的「週期用資料控制」閘門:最新快照距今 < 7 天就跳過。"""

    def test_no_rows_is_due(self):
        self.assertTrue(ft.reddit_snapshot_due([], "2026-08-07"))

    def test_same_day_is_not_due(self):
        rows = [{"date": "2026-08-07"}]
        self.assertFalse(ft.reddit_snapshot_due(rows, "2026-08-07"))

    def test_six_days_old_is_not_due(self):
        rows = [{"date": "2026-08-01"}]
        self.assertFalse(ft.reddit_snapshot_due(rows, "2026-08-07"))

    def test_exactly_seven_days_old_is_due(self):
        rows = [{"date": "2026-07-31"}]
        self.assertTrue(ft.reddit_snapshot_due(rows, "2026-08-07"))

    def test_eight_days_old_is_due(self):
        rows = [{"date": "2026-07-30"}]
        self.assertTrue(ft.reddit_snapshot_due(rows, "2026-08-07"))

    def test_latest_date_wins_over_older_rows(self):
        rows = [{"date": "2026-07-01"}, {"date": "2026-08-05"}, {"date": "2026-07-20"}]
        self.assertFalse(ft.reddit_snapshot_due(rows, "2026-08-07"))

    def test_unparsable_dates_ignored(self):
        rows = [{"date": ""}, {"date": "n/a"}, {"date": "2026-07-30"}]
        self.assertTrue(ft.reddit_snapshot_due(rows, "2026-08-07"))
        self.assertTrue(ft.reddit_snapshot_due([{"date": "n/a"}], "2026-08-07"),
                        "完全讀不出日期時要當成該抓,不得永久卡住")


class TestRedditSave(unittest.TestCase):
    """reddit_weekly.build_save_rows 純函式:同日冪等/新日寫入/欄位形狀(離線,零連網、零檔案 IO)。"""

    def setUp(self):
        import reddit_weekly as rw
        self.rw = rw

    ITEMS = [
        {"title": "Post A", "score": 512, "comments": 88,
         "permalink": "https://www.reddit.com/r/LocalLLaMA/comments/a/",
         "external_url": "https://example.com/a", "body": "Post A body"},
        {"title": "Post B", "score": None, "comments": None,
         "permalink": "https://www.reddit.com/r/LocalLLaMA/comments/b/",
         "external_url": None, "body": ""},
    ]

    def test_new_date_writes_all_rows_in_order(self):
        rows = self.rw.build_save_rows(self.ITEMS, "2026-07-23", existing_rows=[])
        self.assertEqual(rows, [
            ["2026-07-23", 1, "Post A", 512, 88,
             "https://www.reddit.com/r/LocalLLaMA/comments/a/", "https://example.com/a", "Post A body"],
            ["2026-07-23", 2, "Post B", "", "",
             "https://www.reddit.com/r/LocalLLaMA/comments/b/", "", ""],
        ])

    def test_future_csv_schema_has_body_column(self):
        self.assertEqual(self.rw.CSV_HEADER[-1], "body")

    def test_missing_score_and_comments_written_as_empty_string(self):
        # RSS 沒有分數/留言數 → 欄位保留(不改 schema)但寫空字串,不得寫成 "None" 或 0
        rows = self.rw.build_save_rows(self.ITEMS, "2026-07-23", existing_rows=[])
        self.assertEqual(rows[1][3], "")
        self.assertEqual(rows[1][4], "")

    def test_same_day_already_present_skips_write(self):
        existing = [{"date": "2026-07-23", "rank": "1", "title": "Post A"}]
        rows = self.rw.build_save_rows(self.ITEMS, "2026-07-23", existing_rows=existing)
        self.assertEqual(rows, [], "同日已有列 → 冪等跳過,不得重複寫入")

    def test_different_prior_date_does_not_block_write(self):
        existing = [{"date": "2026-07-16", "rank": "1", "title": "Post A"}]
        rows = self.rw.build_save_rows(self.ITEMS, "2026-07-23", existing_rows=existing)
        self.assertEqual(len(rows), 2, "既有列是別的日期時,今天仍應正常寫入")


class TestPhBackfillWindow(unittest.TestCase):
    """PH 歷史回補的 24h 窗:對齊平常每日抓榜口徑(01:00 UTC 收單,回看 24h)。"""

    def setUp(self):
        import backfill_history as bh
        self.bh = bh

    def test_window_ends_at_0100_utc_of_that_day(self):
        after, before = self.bh.ph_window("2026-07-19")
        self.assertEqual(before, "2026-07-19T01:00:00Z")
        self.assertEqual(after, "2026-07-18T01:00:00Z")

    def test_window_is_exactly_24h(self):
        import datetime as dt
        after, before = self.bh.ph_window("2026-07-16")
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        delta = dt.datetime.strptime(before, fmt) - dt.datetime.strptime(after, fmt)
        self.assertEqual(delta, dt.timedelta(hours=24))


if __name__ == "__main__":
    unittest.main()
