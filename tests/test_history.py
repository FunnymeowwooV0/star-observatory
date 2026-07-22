"""
history.py 單元測試(離線,純函式,不碰網路/CSV 檔案)。

守的風險:歷史比較全部靠日期字串比對與純算術,一步算錯(週界/月界、去重鍵、
缺資料時該回 None 卻編了數字)全部無聲壞掉,故逐項情境釘死。

跑法:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import history as h  # noqa: E402


class TestDedupeDailyRows(unittest.TestCase):
    def test_same_day_rerun_keeps_last(self):
        rows = [
            {"date": "2026-07-22", "rank": "1", "repo": "a/old", "new_stars": "100"},
            {"date": "2026-07-22", "rank": "1", "repo": "a/new", "new_stars": "150"},  # 較晚一輪
            {"date": "2026-07-23", "rank": "1", "repo": "b/x", "new_stars": "50"},
        ]
        out = h.dedupe_daily_rows(rows, ["rank"])
        self.assertEqual(len(out), 2)
        d22 = [r for r in out if r["date"] == "2026-07-22"][0]
        self.assertEqual(d22["repo"], "a/new", "同日多輪應保留較晚一輪覆蓋較早一輪")

    def test_multi_key_cols_hf_type_rank(self):
        rows = [
            {"date": "2026-07-22", "type": "模型", "rank": "1", "id": "x/1"},
            {"date": "2026-07-22", "type": "資料集", "rank": "1", "id": "y/1"},
            {"date": "2026-07-22", "type": "模型", "rank": "1", "id": "x/1-rerun"},
        ]
        out = h.dedupe_daily_rows(rows, ["type", "rank"])
        self.assertEqual(len(out), 2, "type+rank 不同鍵應分開保留,同鍵取最後一輪")
        model_row = [r for r in out if r["type"] == "模型"][0]
        self.assertEqual(model_row["id"], "x/1-rerun")

    def test_no_dup_passthrough(self):
        rows = [{"date": "2026-07-22", "rank": "1", "repo": "a"},
                {"date": "2026-07-23", "rank": "1", "repo": "b"}]
        self.assertEqual(len(h.dedupe_daily_rows(rows, ["rank"])), 2)


class TestFindPriorDate(unittest.TestCase):
    def test_normal_next_day(self):
        rows = [{"date": "2026-07-21"}, {"date": "2026-07-22"}]
        self.assertEqual(h.find_prior_date(rows, "2026-07-22"), "2026-07-21")

    def test_skipped_days_after_outage(self):
        # 07-19 抓完後停跑(07-20、07-21 沒資料),07-22 重新開跑
        rows = [{"date": "2026-07-15"}, {"date": "2026-07-19"}]
        self.assertEqual(h.find_prior_date(rows, "2026-07-22"), "2026-07-19",
                         "停跑後重啟,昨日應回溯到停跑前最後一天,不是誤判成沒有昨日")

    def test_no_prior_data_returns_none(self):
        rows = [{"date": "2026-07-22"}]
        self.assertIsNone(h.find_prior_date(rows, "2026-07-22"))

    def test_ignores_same_or_future_dates(self):
        rows = [{"date": "2026-07-22"}, {"date": "2026-07-23"}]
        self.assertIsNone(h.find_prior_date(rows, "2026-07-22"))


class TestRankChanges(unittest.TestCase):
    def test_up_down_same_new(self):
        prior = ["a", "b", "c", "d"]
        today = ["b", "a", "e", "d"]
        changes = h.rank_changes(today, prior)
        self.assertEqual(changes["b"], ("up", 1))     # 2→1
        self.assertEqual(changes["a"], ("down", 1))   # 1→2
        self.assertEqual(changes["e"], ("new", None))
        self.assertEqual(changes["d"], ("same", None))  # 4→4

    def test_format_rank_change(self):
        self.assertEqual(h.format_rank_change(("up", 3)), "↑3")
        self.assertEqual(h.format_rank_change(("down", 2)), "↓2")
        self.assertEqual(h.format_rank_change(("new", None)), "NEW")
        self.assertEqual(h.format_rank_change(("same", None)), "—")

    def test_empty_prior_all_new(self):
        changes = h.rank_changes(["x", "y"], [])
        self.assertEqual(changes["x"], ("new", None))
        self.assertEqual(changes["y"], ("new", None))


class TestSparkSeries(unittest.TestCase):
    def test_direct_mode_normal_and_gap(self):
        rows = [
            {"date": "2026-07-20", "repo": "a", "new_stars": "10"},
            {"date": "2026-07-21", "repo": "a", "new_stars": "20"},
            # 07-22 缺資料(跳日)
            {"date": "2026-07-23", "repo": "a", "new_stars": "5"},
        ]
        series = h.spark_series(rows, "repo", "new_stars", "a", "2026-07-23", days=4)
        self.assertEqual(series, [10, 20, None, 5])

    def test_blank_value_becomes_none(self):
        rows = [{"date": "2026-07-22", "model": "m1", "pulls_delta": ""}]
        series = h.spark_series(rows, "model", "pulls_delta", "m1", "2026-07-22", days=1)
        self.assertEqual(series, [None], "空字串(Ollama 無前一日快照)必須是 None,不得當 0")

    def test_diff_mode_computes_increment_from_last_available_day(self):
        # HF likes 累積值,07-21 有資料、07-22 沒資料(跳日)、07-23 又有資料
        rows = [
            {"date": "2026-07-20", "id": "x/y", "likes": "100"},
            {"date": "2026-07-21", "id": "x/y", "likes": "130"},
            {"date": "2026-07-23", "id": "x/y", "likes": "150"},
        ]
        series = h.spark_series(rows, "id", "likes", "x/y", "2026-07-23", days=4, diff=True)
        # 07-20 無前值→None;07-21=130-100=30;07-22 無資料→None;07-23=150-130(上一個有資料日)=20
        self.assertEqual(series, [None, 30, None, 20])

    def test_diff_mode_no_prior_snapshot_is_none_not_zero(self):
        rows = [{"date": "2026-07-22", "id": "new/item", "likes": "42"}]
        series = h.spark_series(rows, "id", "likes", "new/item", "2026-07-22", days=1, diff=True)
        self.assertEqual(series, [None], "首次上榜無前一日快照,增量必須是 None,不得編成 42 或 0")


class TestSvgSparkline(unittest.TestCase):
    def test_insufficient_points_returns_empty(self):
        self.assertEqual(h.svg_sparkline([None, None, None]), "", "全 None 應回空字串,不畫假線")
        self.assertEqual(h.svg_sparkline([5, None, None]), "", "有效點<2 應回空字串")
        self.assertEqual(h.svg_sparkline([]), "")

    def test_two_valid_points_draws_svg(self):
        svg = h.svg_sparkline([3, None, 7], w=64, h=16)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)
        self.assertEqual(svg.count("<rect"), 2, "None 的那天不該畫 rect")


class TestPeriodBounds(unittest.TestCase):
    def test_week_monday_itself_is_one_day_window(self):
        # 2026-07-20 是週一(isocalendar 驗過)
        start, end = h.period_bounds("week", "2026-07-20")
        self.assertEqual(start, "2026-07-20")
        self.assertEqual(end, "2026-07-20")

    def test_week_midweek_start_is_monday(self):
        # 2026-07-22 週三 → 週一=07-20
        start, end = h.period_bounds("week", "2026-07-22")
        self.assertEqual(start, "2026-07-20")
        self.assertEqual(end, "2026-07-22")

    def test_month_first_day_itself_is_one_day_window(self):
        start, end = h.period_bounds("month", "2026-07-01")
        self.assertEqual(start, "2026-07-01")
        self.assertEqual(end, "2026-07-01")

    def test_month_midmonth_start_is_first(self):
        start, end = h.period_bounds("month", "2026-07-22")
        self.assertEqual(start, "2026-07-01")

    def test_unknown_period_raises(self):
        with self.assertRaises(ValueError):
            h.period_bounds("year", "2026-07-22")

    def test_covered_days_counts_only_actual_data(self):
        rows = [{"date": "2026-07-20"}, {"date": "2026-07-21"}, {"date": "2026-07-25"}]  # 25 超出本週
        start, n = h.covered_days(rows, "week", "2026-07-22")
        self.assertEqual(start, "2026-07-20")
        self.assertEqual(n, 2, "只算落在期間內、且實際有資料的天數")

    def test_covered_days_first_day_of_data_is_one(self):
        # 資料庫今天才誕生(=系統今天),本週/本月都只有 1 天資料
        rows = [{"date": "2026-07-22"}]
        start, n = h.covered_days(rows, "month", "2026-07-22")
        self.assertEqual(n, 1)


class TestPeriodLeaderboard(unittest.TestCase):
    GH_ROWS = [
        {"date": "2026-07-20", "repo": "a/one", "new_stars": "100", "url": "u1", "description": "d1", "language": "Python"},
        {"date": "2026-07-21", "repo": "a/one", "new_stars": "50", "url": "u1", "description": "d1b", "language": "Python"},
        {"date": "2026-07-21", "repo": "b/two", "new_stars": "300", "url": "u2", "description": "d2", "language": "Go"},
        {"date": "2026-07-22", "repo": "c/three", "new_stars": "10", "url": "u3", "description": "d3", "language": "Rust"},
    ]

    def test_sum_type_github_aggregates_and_sorts(self):
        out = h.period_leaderboard(self.GH_ROWS, "week", "2026-07-22", "github", top=10)
        by_name = {e["name"]: e for e in out}
        self.assertEqual(by_name["a/one"]["value"], 150)   # 100+50
        self.assertEqual(by_name["a/one"]["days"], 2)
        self.assertEqual(by_name["b/two"]["value"], 300)
        self.assertEqual(out[0]["name"], "b/two", "應按 Σ 由多到少排序")

    def test_delta_type_hf_uses_first_and_last_snapshot(self):
        rows = [
            {"date": "2026-07-20", "type": "模型", "id": "x/y", "likes": "100", "downloads": "1000", "url": "u"},
            {"date": "2026-07-22", "type": "模型", "id": "x/y", "likes": "180", "downloads": "1500", "url": "u"},
            {"date": "2026-07-20", "type": "資料集", "id": "z/w", "likes": "5", "downloads": "9", "url": "u2"},
        ]
        out = h.period_leaderboard(rows, "week", "2026-07-22", "hf_model", top=10)
        self.assertEqual(len(out), 1, "只應計入 type=模型 的列")
        self.assertEqual(out[0]["likes_delta"], 80)
        self.assertEqual(out[0]["downloads_delta"], 500)

    def test_delta_type_single_snapshot_no_prior_is_none(self):
        rows = [{"date": "2026-07-22", "model": "m1", "pulls": "1000", "url": "u", "desc": "d"}]
        out = h.period_leaderboard(rows, "week", "2026-07-22", "ollama", top=10)
        self.assertIsNone(out[0]["pulls_delta"], "期間內只出現一天,無法算增量,必須是 None 不得編 0")

    def test_max_type_dedupes_by_url_and_keeps_max(self):
        rows = [
            {"date": "2026-07-20", "title": "HN 帖 A", "points": "50", "url": "https://x/a", "hn_url": "hn/a"},
            {"date": "2026-07-21", "title": "HN 帖 A", "points": "120", "url": "https://x/a", "hn_url": "hn/a"},
            {"date": "2026-07-21", "title": "HN 帖 B", "points": "80", "url": "https://x/b", "hn_url": "hn/b"},
        ]
        out = h.period_leaderboard(rows, "week", "2026-07-22", "hn", top=10)
        self.assertEqual(len(out), 2, "同一帖(同 url)出現兩天應去重成一筆")
        by_url = {e["url"]: e for e in out}
        self.assertEqual(by_url["https://x/a"]["value"], 120, "去重後應取期間內最高分,不是相加或取最新")
        self.assertEqual(out[0]["url"], "https://x/a", "應按最高分排序")

    def test_out_of_range_dates_excluded(self):
        out = h.period_leaderboard(self.GH_ROWS, "week", "2026-07-20", "github", top=10)
        names = [e["name"] for e in out]
        self.assertNotIn("c/three", names, "07-22 的資料不該計入以 07-20 為 today 的當週榜(尚未發生)")

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            h.period_leaderboard([], "week", "2026-07-22", "not-a-source")


if __name__ == "__main__":
    unittest.main()
