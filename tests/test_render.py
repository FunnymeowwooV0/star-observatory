"""render_html() 結構與可用性契約測試。

這些測試不碰網路或真實 CSV，只用最小假資料釘死 Task A 的 P0 驗收：
語意結構、導覽、完整內容、手機 GitHub 榜連結、指標誠實性與歷史頁相對路徑。
"""
import html as html_lib
import os
import struct
import sys
import unittest

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_trends as ft  # noqa: E402


LONG_DESCRIPTION = 'A & B <C> "quoted" — ' + "完整內容" * 40


def _repo(i, desc=None):
    return {
        "name": f"owner-{i}/repository-with-a-readable-name-{i}",
        "url": f"https://github.com/owner-{i}/repository-{i}",
        "desc": LONG_DESCRIPTION if desc is None and i == 1 else (desc if desc is not None else f"repo {i} description"),
        "lang": "Python",
        "period_stars": 1100 - i * 10,
        "total_stars": 5000 + i,
    }


def _sample_data():
    gh = [_repo(i) for i in range(1, 11)]
    gh[1]["desc"] = ""
    hf = {
        "模型": [{"id": "org/model", "url": "https://huggingface.co/org/model", "likes": 12,
                "downloads": 345, "tag": "text-generation", "title": ""}],
        "資料集": [{"id": "org/data", "url": "https://huggingface.co/datasets/org/data", "likes": 7,
                 "downloads": 89, "tag": "", "title": ""}],
        "Spaces": [{"id": "org/space", "url": "https://huggingface.co/spaces/org/space", "likes": 5,
                   "downloads": None, "tag": "Chatbots", "title": ""}],
    }
    leaderboards = {
        "week": {
            "start": "2026-07-20",
            "days": 2,
            "sources": {
                "github": [{"name": gh[0]["name"], "url": gh[0]["url"], "value": 2100, "days": 2}],
                "openrouter": [{"name": "vendor/model name+v1", "value": 987654}],
                "hf_model": [{"name": "org/model", "url": "https://huggingface.co/org/model",
                              "likes_delta": 4, "downloads_delta": 120}],
                "ollama": [{"name": "qwen3", "url": "https://ollama.com/library/qwen3",
                            "pulls_delta": 900}],
                "hn": [{"name": "A useful HN story", "url": "https://example.com/story", "value": 321}],
                "ph": [{"name": "Useful Product", "url": "https://www.producthunt.com/posts/useful-product",
                        "value": 88}],
            },
        },
        "month": {
            "start": "2026-07-01",
            "days": 22,
            "sources": {
                "github": [{"name": gh[1]["name"], "url": gh[1]["url"],
                            "value": 4200, "days": 7}],
            },
        },
    }
    return gh, hf, leaderboards


def _source_data():
    return {
        "hn": [{"title": "A useful HN story", "points": 321, "comments": 45,
                "url": "https://example.com/story",
                "hn_url": "https://news.ycombinator.com/item?id=1"}],
        "openrouter": [{"model": "vendor/model name+v1", "total_tokens": 987654,
                        "prompt_tokens": 654321, "completion_tokens": 333333}],
        "ph": [{"name": "Useful Product", "tagline": "A practical launch tool", "votes": 88,
                "url": "https://www.producthunt.com/posts/useful-product"}],
        "ollama": [{"name": "qwen3", "pulls": 1234567, "caps": ["tools", "thinking"],
                    "url": "https://ollama.com/library/qwen3", "desc": "A capable local model",
                    "updated": "2 days ago"}],
        "ollama_deltas": {"qwen3": 900},
    }


_UNSET = object()


def _png_size(path):
    with open(path, "rb") as image:
        self_header = image.read(24)
    if self_header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"不是 PNG:{path}")
    return struct.unpack(">II", self_header[16:24])


def _render(*, errors=None, snapshot_date=None, hn=_UNSET, leaderboards=_UNSET):
    gh, hf, sample_leaderboards = _sample_data()
    sources = _source_data()
    actual_hn = sources["hn"] if hn is _UNSET else hn
    actual_leaderboards = sample_leaderboards if leaderboards is _UNSET else leaderboards
    return ft.render_html(
        "2026-07-22",
        "2026-07-22 09:00 UTC+08:00",
        gh,
        hf,
        errors or [],
        hn=actual_hn,
        openrouter=sources["openrouter"],
        ph=sources["ph"],
        ollama=sources["ollama"],
        ollama_deltas=sources["ollama_deltas"],
        sparks={"hf_model": {"org/model": '<svg class="spark"></svg>'},
                "ollama": {"qwen3": '<svg class="spark"></svg>'}},
        leaderboards=actual_leaderboards,
        history_dates=["2026-07-21", "2026-07-22"],
        snapshot_date=snapshot_date,
    )


class TestRenderHtmlTaskA(unittest.TestCase):
    def test_github_category_inference_uses_tokens_priority_and_fallback(self):
        cases = [
            (("org/agent-kit", "Developer monitoring dashboard"), "AI／LLM"),
            (("org/worldmonitor", "global analytics dashboard"), "資料與監測"),
            (("org/reviewer", "CLI code review SDK"), "開發工具"),
            (("org/focus", "ADHD task and notes workflow"), "生產力"),
            (("org/edge", "cloud deploy proxy server"), "基礎設施"),
            (("org/openship", "Self-hosted deployment platform"), "基礎設施"),
            (("org/store", "mobile ecommerce platform"), "網站／應用"),
            (("org/daily", "a delightful utility"), "其他工具"),
            (("", ""), "其他工具"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(ft.infer_github_category(*args), expected)

    def test_semantic_landmarks_and_prominent_navigation(self):
        soup = BeautifulSoup(_render(), "html.parser")

        self.assertIsNotNone(soup.select_one('a.skip-link[href="#main-content"]'))
        self.assertIsNotNone(soup.select_one("main#main-content"))
        self.assertEqual(soup.select_one("h1 .title-date").get_text(strip=True), "2026-07-22 ·")
        self.assertEqual(soup.select_one("h1 .title-topic").get_text(strip=True), "開源與科技熱門觀測")
        theme_colors = {(tag.get("media"), tag.get("content")) for tag in soup.select('meta[name="theme-color"]')}
        self.assertEqual(theme_colors, {
            ("(prefers-color-scheme: light)", "#08206b"),
            ("(prefers-color-scheme: dark)", "#061747"),
        })
        nav = soup.select_one('nav[aria-label="主要導覽"]')
        self.assertIsNotNone(nav)
        self.assertIsNotNone(nav.select_one('a[role="tab"][href="#today"]'))
        self.assertIsNotNone(nav.select_one('a[role="tab"][href="#leaderboards"]'))
        self.assertIn("看歷史", nav.get_text(" ", strip=True))
        self.assertIsNotNone(soup.select_one('#today[role="tabpanel"]'))
        self.assertIsNotNone(soup.select_one('#leaderboards[role="tabpanel"]'))

    def test_tabs_have_accessible_progressive_enhancement_markup(self):
        soup = BeautifulSoup(_render(), "html.parser")
        tablist = soup.select_one('[data-tab-group="page"][role="tablist"]')
        tabs = tablist.select('a[role="tab"]') if tablist else []

        self.assertIsNotNone(tablist)
        self.assertEqual([tab["aria-controls"] for tab in tabs], ["today", "leaderboards"])
        self.assertEqual([tab["aria-selected"] for tab in tabs], ["true", "false"])
        self.assertEqual([tab["tabindex"] for tab in tabs], ["0", "-1"])
        self.assertNotIn("role", soup.select_one(".history-picker").attrs)
        for panel_id in ("today", "leaderboards"):
            panel = soup.select_one(f'#{panel_id}[role="tabpanel"][data-tab-panel="page"]')
            self.assertIsNotNone(panel)
            self.assertFalse(panel.has_attr("hidden"))

        groups = [node["data-tab-group"] for node in soup.select('[data-tab-group][role="tablist"]')]
        self.assertEqual(groups[:3], ["page", "source", "leaderboard-source"])
        self.assertEqual(set(groups[3:]), {
            "leaderboard-period-github",
            "leaderboard-period-hf_model",
            "leaderboard-period-hn",
            "leaderboard-period-openrouter",
            "leaderboard-period-ph",
            "leaderboard-period-ollama",
        })
        self.assertEqual(soup.select_one('[data-tab-group="source"]')["data-parent-panel"], "today")
        self.assertEqual(
            soup.select_one('[data-tab-group="leaderboard-source"]')["data-parent-panel"],
            "leaderboards",
        )
        for tablist in soup.select('[data-tab-group^="leaderboard-period-"]'):
            source_key = tablist["data-tab-group"].removeprefix("leaderboard-period-")
            self.assertEqual(
                tablist["data-parent-panel"],
                f"leaderboard-source-{source_key}",
            )

        script = soup.select_one("script[data-tab-controller]")
        self.assertIsNotNone(script)
        script_text = script.get_text()
        for token in ("data-tab-group", "data-parent-panel", "data-tab-panel",
                      "historyTargetId", "history.pushState", "popstate", "hashchange",
                      "selectAncestors", "groupForTarget",
                      "ArrowLeft", "ArrowRight",
                      "Home", "End", "scrollIntoView"):
            self.assertIn(token, script_text)

    def test_history_picker_displays_latest_snapshot_date(self):
        soup = BeautifulSoup(_render(), "html.parser")
        selected = soup.select_one("#history-date option[selected]")
        trigger = soup.select_one("button.history-trigger")
        calendar = soup.select_one(".calendar-shell")
        picker = soup.select_one("#history-date")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.get_text(strip=True), "2026-07-22")
        self.assertEqual(selected.get("value"), "history/2026-07-22.html")
        self.assertNotIn("選擇日期", picker.get_text())
        self.assertIsNotNone(trigger)
        self.assertIsNotNone(calendar)
        self.assertEqual(trigger.get_text(strip=True), "看歷史")
        self.assertEqual(trigger["aria-expanded"], "false")
        self.assertTrue(picker.has_attr("disabled"))
        self.assertEqual(calendar["aria-disabled"], "true")
        self.assertIsNotNone(calendar.select_one("svg.calendar-icon"))

        script_text = soup.select_one("script[data-tab-controller]").get_text()
        for token in ("historyTrigger", "historySelect", "aria-expanded", "is-active"):
            self.assertIn(token, script_text)

    def test_history_picker_has_compact_neutral_focus_and_pressed_only_underline(self):
        css = BeautifulSoup(_render(), "html.parser").style.get_text()

        self.assertIn(".history-trigger:active::after{transform:scaleX(1)}", css)
        self.assertNotIn('.history-trigger[aria-expanded="true"]::after', css)
        self.assertIn(".date-switch:focus-visible{outline:none}", css)
        self.assertIn(
            ".calendar-shell:focus-within{box-shadow:inset 0 0 0 1px",
            css,
        )
        for token in (
            ".history-picker{grid-column:2;justify-self:start;display:flex;align-items:center;gap:8px",
            ".calendar-shell{display:inline-flex;align-items:center;min-height:36px",
            ".calendar-icon{flex:0 0 auto;width:15px;height:15px;margin-left:8px",
            ".date-switch{min-height:34px;max-width:128px;padding:3px 24px 3px 6px",
            ".calendar-shell{min-height:44px}.date-switch{min-height:44px}",
        ):
            self.assertIn(token, css)

    def test_today_has_eight_independent_source_tabs_and_panels(self):
        soup = BeautifulSoup(_render(), "html.parser")
        tablist = soup.select_one('[data-tab-group="source"][role="tablist"]')
        tabs = tablist.select(':scope > [role="tab"]') if tablist else []
        controls = [tab["aria-controls"] for tab in tabs]

        self.assertEqual(controls, [
            "github-focus", "hf-models", "hf-datasets", "hf-spaces",
            "hacker-news", "openrouter", "product-hunt", "ollama",
        ])
        self.assertEqual([tab.get_text(" ", strip=True) for tab in tabs], [
            "GitHub", "HF 模型", "HF 資料集", "HF Spaces",
            "Hacker News", "OpenRouter", "Product Hunt", "Ollama",
        ])
        self.assertEqual([tab["aria-selected"] for tab in tabs], ["true"] + ["false"] * 7)
        for control, tab in zip(controls, tabs):
            panel = soup.select_one(f'#{control}[data-tab-panel="source"]')
            self.assertIsNotNone(panel)
            self.assertEqual(panel["role"], "tabpanel")
            self.assertEqual(panel["aria-labelledby"], tab["id"])
            self.assertFalse(panel.has_attr("hidden"))
        self.assertIsNone(soup.select_one("#hugging-face"))

    def test_accumulation_has_source_tabs_with_period_tabs_inside_each_source(self):
        soup = BeautifulSoup(_render(), "html.parser")
        source_list = soup.select_one('[data-tab-group="leaderboard-source"][role="tablist"]')
        source_tabs = source_list.select(':scope > [role="tab"]') if source_list else []

        self.assertEqual([tab["aria-controls"] for tab in source_tabs], [
            "leaderboard-source-github",
            "leaderboard-source-hf_model",
            "leaderboard-source-hn",
            "leaderboard-source-openrouter",
            "leaderboard-source-ph",
            "leaderboard-source-ollama",
        ])
        self.assertEqual([tab.get_text(" ", strip=True) for tab in source_tabs], [
            "GitHub", "HF 模型", "Hacker News", "OpenRouter", "Product Hunt", "Ollama",
        ])
        for tab in source_tabs:
            panel = soup.select_one(
                f'#{tab["aria-controls"]}[data-tab-panel="leaderboard-source"]'
            )
            self.assertIsNotNone(panel)
            self.assertEqual(panel["aria-labelledby"], tab["id"])
            self.assertFalse(panel.has_attr("hidden"))

        github_periods = soup.select(
            '#leaderboard-source-github [data-tab-group="leaderboard-period-github"] '
            '> [role="tab"]'
        )
        self.assertEqual([tab["aria-controls"] for tab in github_periods], [
            "leaderboard-github-week", "leaderboard-github-month",
        ])
        self.assertEqual([tab.get_text(strip=True) for tab in github_periods], ["本週", "本月"])
        for tab in github_periods:
            panel = soup.select_one(
                f'#{tab["aria-controls"]}[data-tab-panel="leaderboard-period-github"]'
            )
            self.assertIsNotNone(panel)
            self.assertEqual(panel["aria-labelledby"], tab["id"])
        text = soup.select_one("#leaderboards").get_text(" ", strip=True)
        self.assertNotIn("累積排行榜", text)
        self.assertNotIn("本週累積榜", text)
        self.assertNotIn("本月累積榜", text)

    def test_missing_leaderboards_omits_second_tab_and_panel(self):
        soup = BeautifulSoup(_render(leaderboards={}), "html.parser")
        tabs = soup.select('[data-tab-group="page"][role="tablist"] a[role="tab"]')

        self.assertEqual([tab["aria-controls"] for tab in tabs], ["today"])
        self.assertIsNone(soup.select_one("#leaderboards"))
        self.assertIsNotNone(soup.select_one('#today[role="tabpanel"]'))
        self.assertIsNone(soup.select_one('[data-tab-group="leaderboard-source"]'))
        self.assertIsNone(soup.select_one('[data-tab-group^="leaderboard-period-"]'))

    def test_github_top_ten_is_one_clickable_card_per_repo(self):
        gh, _hf, _leaderboards = _sample_data()
        soup = BeautifulSoup(_render(), "html.parser")
        section = soup.select_one("section#github-focus")
        cards = section.select("a.card.gh-card") if section else []

        self.assertIsNotNone(section)
        self.assertIn("GitHub 今日焦點 Top 10", section.get_text(" ", strip=True))
        self.assertIn("用途標籤依專案名稱與公開簡介規則推定", section.get_text(" ", strip=True))
        self.assertEqual(len(cards), 10)
        self.assertEqual([card["href"] for card in cards], [r["url"] for r in gh])
        for i, (card, repo) in enumerate(zip(cards, gh), 1):
            text = card.get_text(" ", strip=True)
            self.assertIn(f"#{i:02d}", text)
            self.assertIn(repo["name"], text)
            self.assertIn(f'+{repo["period_stars"]:,}', text)
            self.assertIn("今日新增星", text)
            self.assertIn(f'{repo["total_stars"]:,} 總星', text)
            self.assertIn(repo["lang"], text)
            self.assertIsNotNone(card.select_one(".category"))
            self.assertEqual(len(card.select("a")), 0)

        self.assertIsNone(soup.select_one(".bars"))
        self.assertIsNone(soup.select_one(".bar-row"))
        self.assertIsNone(soup.select_one(".bar-spark"))
        self.assertIsNone(soup.select_one(".card-link"))
        self.assertNotIn("焦點前五", soup.get_text(" ", strip=True))

    def test_description_keeps_full_text_and_uses_consistent_empty_state(self):
        raw_html = _render()
        soup = BeautifulSoup(raw_html, "html.parser")
        descriptions = soup.select(".card-desc")

        self.assertEqual(descriptions[0].get_text(strip=True), LONG_DESCRIPTION)
        self.assertIn(html_lib.escape(LONG_DESCRIPTION), raw_html)
        self.assertEqual(descriptions[1].get_text(strip=True), "暫無說明")
        self.assertIn("-webkit-line-clamp:2", raw_html)

    def test_hf_uses_named_metrics_and_omits_missing_space_downloads(self):
        soup = BeautifulSoup(_render(), "html.parser")
        model_section = soup.select_one("section#hf-models")
        space_section = soup.select_one("section#hf-spaces")
        self.assertIsNotNone(model_section)
        self.assertIsNotNone(space_section)
        self.assertIn("官方 Trending API", model_section.get_text(" ", strip=True))
        self.assertIn("互動應用（Spaces）Top 1", space_section.get_text(" ", strip=True))
        self.assertIn("可直接操作的機器學習 Demo／應用，不是模型",
                      space_section.get_text(" ", strip=True))

        model_card = model_section.find(string="org/model").find_parent(
            "a", class_="ranking-card",
        )
        self.assertIn("12 Likes", model_card.get_text(" ", strip=True))
        self.assertIn("下載 345", model_card.get_text(" ", strip=True))

        space_card = space_section.find(string="org/space").find_parent(
            "a", class_="ranking-card",
        )
        self.assertIn("5 Likes", space_card.get_text(" ", strip=True))
        self.assertNotIn("下載", space_card.get_text(" ", strip=True))
        self.assertNotIn("⬇ —", space_card.get_text(" ", strip=True))

    def test_non_hn_daily_sources_use_github_style_card_grid(self):
        soup = BeautifulSoup(_render(), "html.parser")
        selectors = {
            "#hf-models": 1,
            "#hf-datasets": 1,
            "#hf-spaces": 1,
            "#openrouter": 1,
            "#product-hunt": 1,
            "#ollama": 1,
        }
        for selector, expected_count in selectors.items():
            with self.subTest(selector=selector):
                cards = soup.select(f"{selector} .cards > a.card.ranking-card")
                self.assertEqual(len(cards), expected_count)
                self.assertIsNone(soup.select_one(f"{selector} .link-card-list"))

        openrouter_card = soup.select_one("#openrouter a.ranking-card")
        self.assertEqual(openrouter_card["href"], "https://openrouter.ai/vendor/model%20name%2Bv1")
        self.assertIn("Σ 987,654 tokens", openrouter_card.get_text(" ", strip=True))
        self.assertEqual(soup.select_one("#product-hunt a.ranking-card")["href"],
                         "https://www.producthunt.com/posts/useful-product")
        ollama_card = soup.select_one("#ollama a.ranking-card")
        self.assertEqual(ollama_card["href"], "https://ollama.com/library/qwen3")
        self.assertIn("A capable local model", ollama_card.get_text(" ", strip=True))
        self.assertIsNotNone(soup.select_one("#hacker-news .link-card-list"))
        self.assertIsNone(soup.select_one("#hacker-news .cards"))
        self.assertIsNone(soup.select_one("#hf-models .spark"))
        self.assertIsNone(soup.select_one("#hf-datasets .spark"))
        self.assertIsNone(soup.select_one("#hf-spaces .spark"))
        self.assertIsNone(soup.select_one("#ollama .spark"))

    def test_hn_keeps_separate_discussion_target_without_nested_links(self):
        soup = BeautifulSoup(_render(), "html.parser")
        row = soup.select_one("#hacker-news li.hn-card-row")

        self.assertIsNotNone(row)
        self.assertEqual(len(row.find_all("a", recursive=False)), 2)
        self.assertIsNotNone(row.select_one("a.link-card"))
        self.assertIsNotNone(row.select_one("a.hn-discussion"))
        self.assertIsNone(row.select_one("a a"))

        same_target = [{"title": "Ask HN", "points": 10, "comments": 3,
                        "url": "https://news.ycombinator.com/item?id=2",
                        "hn_url": "https://news.ycombinator.com/item?id=2"}]
        same_soup = BeautifulSoup(_render(hn=same_target), "html.parser")
        same_row = same_soup.select_one("#hacker-news li.hn-card-row")
        self.assertEqual(len(same_row.find_all("a", recursive=False)), 1)

    def test_non_hn_accumulation_uses_github_style_card_grid(self):
        soup = BeautifulSoup(_render(), "html.parser")
        cards = soup.select("#leaderboards .cards > a.card.ranking-card")
        hn_rows = soup.select(
            "#leaderboard-source-hn ol.link-card-list > li.link-card-item",
        )
        css = soup.style.get_text()

        self.assertEqual([source[4] for source in ft.LEADERBOARD_SOURCES], [10] * 8)
        self.assertEqual(len(cards), 6)
        self.assertEqual(len(hn_rows), 1)
        for source in ("github", "hf_model", "hf_dataset", "hf_space",
                       "openrouter", "ph", "ollama"):
            panel = soup.select_one(f"#leaderboard-source-{source}")
            if panel is not None:
                self.assertIsNone(panel.select_one(".link-card-list"))
        self.assertEqual(soup.select_one(
            '#leaderboards a.ranking-card[href^="https://openrouter.ai/"]',
        )["href"],
                         "https://openrouter.ai/vendor/model%20name%2Bv1")
        single_column_rule = ".hf-cols.leaderboard-list{grid-template-columns:1fr}"
        self.assertIn(single_column_rule, css)
        self.assertGreater(css.index(single_column_rule), css.index(".hf-cols{display:grid"))

    def test_blank_url_renders_static_link_card_without_empty_href(self):
        soup = BeautifulSoup(ft._link_card_item(1, "No destination", "No URL", ""), "html.parser")

        self.assertIsNone(soup.select_one("a"))
        self.assertIsNotNone(soup.select_one(".link-card.link-card-static"))

    def test_truthful_github_metric_copy_is_adjacent_to_ranking(self):
        soup = BeautifulSoup(_render(), "html.parser")
        today = soup.select_one("#today")
        text = today.get_text(" ", strip=True)

        self.assertIn("GitHub 今日焦點 Top 10", text)
        self.assertNotIn("GitHub 24 小時成長排行", text)
        self.assertIn("24 小時動能代理值", text)
        self.assertIn("非精確", text)

    def test_partial_source_errors_are_a_visible_alert_and_escaped(self):
        raw_html = _render(errors=['HF 模型抓取失敗:<bad & "unsafe">'])
        soup = BeautifulSoup(raw_html, "html.parser")
        alert = soup.select_one('.err[role="alert"]')

        self.assertIsNotNone(alert)
        self.assertIn('HF 模型抓取失敗:<bad & "unsafe">', alert.get_text(strip=True))
        self.assertIn("稍後重新整理", alert.get_text(strip=True))
        self.assertNotIn("<bad", raw_html)

    def test_external_links_have_no_decorative_arrows_and_keep_noopener(self):
        raw_html = _render(snapshot_date="2026-07-22")
        soup = BeautifulSoup(raw_html, "html.parser")
        external_links = soup.select('a[target="_blank"]')

        self.assertTrue(external_links)
        for link in external_links:
            self.assertIn("noopener", link.get("rel", []))
        css = soup.style.get_text()
        self.assertNotIn('a[target="_blank"]::after', css)
        self.assertNotIn("↗", raw_html)
        self.assertNotIn("回今日 →", raw_html)
        self.assertIn(":focus-visible", css)
        self.assertIn(":active", css)
        self.assertIn("translateY(-2px) scale(1.005)", css)
        self.assertIn("translateY(0) scale(.995)", css)
        self.assertIn("140ms", css)
        self.assertIn("touch-action:manipulation", css)
        self.assertIn("-webkit-tap-highlight-color", css)
        self.assertIn("color-scheme:light dark", css)
        self.assertIn("text-wrap:balance", css)

    def test_night_sky_visual_system_uses_approved_background_asset(self):
        soup = BeautifulSoup(_render(), "html.parser")
        css = soup.style.get_text()
        font_urls = [link.get("href", "") for link in soup.select('link[rel="stylesheet"]')]

        self.assertTrue(any("fonts.googleapis.com" in url for url in font_urls))
        for family in ("Noto Serif TC", "Noto Sans TC", "IBM Plex Mono"):
            self.assertIn(family, css)
        for token in ("--sky-top:#08206b", "--sky-mid:#07184f", "--paper:#f2e9d2",
                      'background-image:url("assets/observatory-night-sky-4k.webp")',
                      'background-image:url("assets/observatory-night-sky-mobile-4k.webp")',
                      "background-size:100% 100%", "background-repeat:no-repeat"):
            self.assertIn(token, css)
        self.assertNotIn("feTurbulence", css)
        self.assertNotIn("body::before", css)
        self.assertNotIn("body::after", css)
        self.assertIsNone(soup.select_one(".orbit"))
        self.assertIsNotNone(soup.select_one('.sky-art[aria-hidden="true"]'))

        history_css = BeautifulSoup(
            _render(snapshot_date="2026-07-22"), "html.parser"
        ).style.get_text()
        self.assertIn(
            'background-image:url("../assets/observatory-night-sky-4k.webp")',
            history_css,
        )
        self.assertIn(
            'background-image:url("../assets/observatory-night-sky-mobile-4k.webp")',
            history_css,
        )

        asset = os.path.join(
            os.path.dirname(__file__), "..", "docs", "assets",
            "observatory-night-sky-4k.webp",
        )
        self.assertTrue(os.path.isfile(asset))
        self.assertGreater(os.path.getsize(asset), 100_000)
        mobile_asset = os.path.join(
            os.path.dirname(__file__), "..", "docs", "assets",
            "observatory-night-sky-mobile-4k.webp",
        )
        self.assertTrue(os.path.isfile(mobile_asset))
        self.assertGreater(os.path.getsize(mobile_asset), 100_000)

        source_sizes = {
            "observatory-night-sky-4k.png": (3840, 2160),
            "observatory-night-sky-mobile-4k.png": (2160, 3840),
            "observatory-night-sky-4k-noise.png": (3840, 2160),
            "observatory-night-sky-mobile-4k-noise.png": (2160, 3840),
        }
        for filename, expected_size in source_sizes.items():
            with self.subTest(filename=filename):
                source = os.path.join(
                    os.path.dirname(__file__), "..", "design-assets", filename,
                )
                self.assertEqual(_png_size(source), expected_size)

    def test_desktop_navigation_matches_approved_full_width_editorial_layout(self):
        css = BeautifulSoup(_render(), "html.parser").style.get_text()

        for token in (
            "width:90%;max-width:1384px",
            ".page-nav{display:grid;grid-template-columns:auto minmax(0,1fr)",
            ".history-picker{grid-column:2;justify-self:start;display:flex",
            ".calendar-shell.is-active",
            '.page-tab[aria-selected="true"]::after',
            ".source-tab-list{width:100%",
            ".source-tab-list>.source-tab{flex:1 1 0",
            "background:rgba(4,15,54,.18)",
        ):
            self.assertIn(token, css)
        self.assertNotIn(".tab-list{display:flex;align-items:center;border:1px", css)

    def test_approved_editorial_card_and_mobile_tab_css_is_present(self):
        css = BeautifulSoup(_render(), "html.parser").style.get_text()

        for token in ("overflow-x:auto", "overscroll-behavior-inline:contain",
                      "scrollbar-width:none", "grid-template-columns:64px minmax(0,1fr)",
                      "grid-template-columns:repeat(2,minmax(0,1fr))",
                      "-webkit-line-clamp:2", "min-height:44px"):
            self.assertIn(token, css)
        self.assertIn("font-variant-numeric:tabular-nums;white-space:nowrap", css)
        self.assertIn(
            ".source-tab-list>.source-tab{flex:0 0 auto;min-width:max-content}",
            css,
        )
        self.assertNotIn(".hf-col{background:var(--card);border:1px", css)

        soup = BeautifulSoup(_render(), "html.parser")
        cards = soup.select("#github-focus .gh-card")
        self.assertEqual(len(cards), 10)
        for card in cards:
            self.assertIsNotNone(card.select_one(":scope > .rank"))
            self.assertIsNotNone(card.select_one(":scope > .card-content"))
            self.assertIsNotNone(card.select_one(":scope > .card-metrics"))

    def test_status_marks_use_readable_ink_colours(self):
        css = BeautifulSoup(_render(), "html.parser").style.get_text()

        self.assertIn("--up:#0f6e56", css)
        self.assertIn("--down:#b23b3b", css)
        self.assertIn("--new:#185fa5", css)
        self.assertIn(".mark.up{color:var(--up)}", css)
        self.assertIn(".mark.down{color:var(--down)}", css)
        self.assertIn(".mark.new{color:var(--new)}", css)

    def test_snapshot_navigation_uses_relative_paths(self):
        soup = BeautifulSoup(_render(snapshot_date="2026-07-22"), "html.parser")
        nav = soup.select_one('nav[aria-label="主要導覽"]')

        self.assertEqual(nav.find("a", string="今日榜")["href"], "../index.html#today")
        self.assertEqual(nav.find("a", string="累積榜")["href"], "#leaderboards")
        values = [opt.get("value") for opt in nav.select("select option") if opt.get("value")]
        self.assertEqual(values, ["2026-07-22.html", "2026-07-21.html"])
        self.assertEqual(soup.select_one(".snapshot-banner a")["href"], "../index.html")


if __name__ == "__main__":
    unittest.main()
