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
            self.assertIsInstance(it["votes"], int)
            self.assertTrue(it["url"].startswith("https://"), it["url"])

    def test_empty_input_returns_empty(self):
        self.assertEqual(se.parse_ph_posts({}), [])
        self.assertEqual(se.parse_ph_posts({"data": {"posts": {"edges": []}}}), [])


if __name__ == "__main__":
    unittest.main()
