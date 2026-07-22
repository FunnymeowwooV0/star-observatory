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

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


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


if __name__ == "__main__":
    unittest.main()
