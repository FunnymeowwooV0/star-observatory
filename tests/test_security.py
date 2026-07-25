"""資安契約測試:外部 URL scheme allowlist(P1-1)與 CSV 公式注入中和(P2-3)。

這些測試不碰網路,只用純函式與 render_html 的最小假資料,釘死:
- safe_external_url() 只放行 https 絕對網址,擋掉 javascript/data/vbscript/file/mailto、
  protocol-relative、空 scheme、userinfo,以及大小寫/空白/控制字元 evasion。
- 被污染的上游 URL 不會進入產出 HTML 的 href。
- _csv_safe_cell() 對以 = + - @ 開頭的字串前綴單引號中和,數字欄不受影響。
"""
import csv
import io
import os
import sys
import unittest

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_trends as ft  # noqa: E402


DANGEROUS = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "java\rscript:alert(1)",
    "\x00javascript:alert(1)",
    "\x01javascript:alert(1)",
    "jAvAsCrIpT:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "VBScript:msgbox(1)",
    "file:///etc/passwd",
    "mailto:a@b.com",
    "//evil.example.com/path",
    "\t//evil.example.com",
    "/relative/path",
    "relative/path",
    "https://user@evil.example.com",
    "https://user:pass@evil.example.com",
    "http://insecure.example.com",  # 只允許 https
    "https:evil",  # 無 host
    "",
    "   ",
    "\t\r\n",
]

SAFE = [
    "https://github.com/owner/repo",
    "https://huggingface.co/org/model",
    "https://news.ycombinator.com/item?id=123",
    "https://www.reddit.com/r/LocalLLaMA/comments/abc/x/",
    "https://www.producthunt.com/posts/useful-product",
    "https://ollama.com/library/qwen3",
    "https://openrouter.ai/vendor/model",
    "https://example.com/path?q=1&x=2#frag",
    "https://例え.jp/パス",
    "https://example.com/%E4%B8%AD%E6%96%87",
    "HTTPS://example.com/upper-scheme-ok",
]


class TestSafeExternalUrl(unittest.TestCase):
    def test_dangerous_urls_become_empty(self):
        for u in DANGEROUS:
            with self.subTest(url=repr(u)):
                self.assertEqual(ft.safe_external_url(u), "")

    def test_safe_https_urls_pass_through_unchanged(self):
        for u in SAFE:
            with self.subTest(url=repr(u)):
                self.assertEqual(ft.safe_external_url(u), u)

    def test_none_returns_empty(self):
        self.assertEqual(ft.safe_external_url(None), "")


class TestSinkFiltering(unittest.TestCase):
    def test_ranking_card_drops_javascript_url(self):
        html = ft._ranking_card(1, "poison", "javascript:alert(1)", primary="1")
        self.assertNotIn("javascript:", html)
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(soup.select_one("a"))
        self.assertIsNotNone(soup.select_one(".ranking-card-static"))

    def test_link_card_item_drops_data_url(self):
        html = ft._link_card_item(1, "poison", "meta", "data:text/html,<script>")
        self.assertNotIn("data:text/html", html)
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(soup.select_one("a"))

    def test_safe_url_still_renders_anchor(self):
        html = ft._ranking_card(1, "ok", "https://github.com/x/y", primary="1")
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(soup.select_one("a")["href"], "https://github.com/x/y")


class TestRenderRejectsPoisonedUpstream(unittest.TestCase):
    def _render_poisoned(self):
        gh = [{"name": "org/repo", "url": "javascript:alert('gh')", "desc": "d",
               "lang": "Python", "period_stars": 100, "total_stars": 500}]
        hf = {"模型": [], "資料集": [], "Spaces": []}
        hn = [{"title": "poison hn", "points": 10, "comments": 2,
               "url": "javascript:alert('hn')",
               "hn_url": "javascript:alert('hndisc')"}]
        ph = [{"name": "poison ph", "tagline": "t", "votes": 5,
               "url": "data:text/html,<script>alert('ph')</script>"}]
        reddit = [{"title": "poison reddit", "score": 9, "comments": 1,
                   "permalink": "javascript:alert('rp')",
                   "external_url": "vbscript:msgbox('re')"}]
        return ft.render_html(
            "2026-07-22", "2026-07-22 09:00 UTC+08:00", gh, hf, [],
            hn=hn, openrouter=[], ph=ph, ollama=[], ollama_deltas={},
            reddit=reddit, reddit_snapshot_date="2026-07-22",
            sparks={}, leaderboards=None, history_dates=[], snapshot_date=None,
        )

    def test_no_dangerous_scheme_in_any_href(self):
        html = self._render_poisoned()
        for bad in ("javascript:", "data:text/html", "vbscript:"):
            self.assertNotIn(bad, html, f"{bad} 出現在產出 HTML")

    def test_poisoned_cards_have_no_anchor_href_to_upstream(self):
        soup = BeautifulSoup(self._render_poisoned(), "html.parser")
        for a in soup.select("a[href]"):
            href = a["href"]
            self.assertFalseUnsafe(href)

    def assertFalseUnsafe(self, href):
        low = href.strip().lower().replace("\t", "").replace("\n", "").replace("\r", "")
        for bad in ("javascript:", "data:", "vbscript:", "file:", "mailto:"):
            self.assertFalse(low.startswith(bad), f"unsafe href leaked: {href}")


class TestCsvFormulaInjection(unittest.TestCase):
    def test_formula_prefixes_are_neutralized(self):
        for cell in ("=HYPERLINK(\"x\")", "+1+1", "-2+3", "@SUM(A1)",
                     "\t=cmd", "\r=cmd", "\n=cmd", "  =cmd"):
            with self.subTest(cell=cell):
                self.assertEqual(ft._csv_safe_cell(cell), "'" + cell)

    def test_normal_strings_untouched(self):
        for cell in ("owner/repo", "A normal title", "https://x/y",
                     "中文標題", "", "price is 5"):
            with self.subTest(cell=cell):
                self.assertEqual(ft._csv_safe_cell(cell), cell)

    def test_non_strings_untouched(self):
        for cell in (5, 0, -3, None, 1234567):
            with self.subTest(cell=cell):
                self.assertEqual(ft._csv_safe_cell(cell), cell)

    def test_append_csv_neutralizes_string_cells(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rel = "data/_sec_test.csv"
            orig_root = ft.ROOT
            ft.ROOT = d
            try:
                ft.append_csv(rel, ["a", "b", "c"],
                              [["=HYPERLINK(\"evil\")", 42, "normal"]])
            finally:
                ft.ROOT = orig_root
            with open(os.path.join(d, rel), encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[1][0], "'=HYPERLINK(\"evil\")")
            self.assertEqual(rows[1][1], "42")
            self.assertEqual(rows[1][2], "normal")


if __name__ == "__main__":
    unittest.main()
