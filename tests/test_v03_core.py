"""V0.3 orchestration tests; all external providers are offline fakes."""

import json
import tempfile
import unittest
from unittest.mock import AsyncMock

from core import Explorer, Store, format_record


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def source(n):
    return {"url": f"https://example.com/{n}", "title": f"标题{n}", "excerpt": f"摘要{n}"}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.search = AsyncMock(side_effect=[([source(1), source(2)], []), ([source(3)], [])])
        self.discover = AsyncMock(return_value=([source(4)], []))
        self.extract = AsyncMock(
            side_effect=lambda sources, **kw: (
                [
                    dict(s, read_level="full_text", read_content="真实正文讨论音乐历史")
                    for s in sources[: kw["maximum"]]
                ],
                [],
            )
        )
        self.plan = {
            "exploration_mode": "discovery",
            "topic": "新生活领域",
            "keywords": ["音乐"],
            "discovery_queries": ["音乐历史", "自然观察"],
        }
        self.choice = {"selected": [{"source_id": 2, "reason": "想了解音乐历史"}]}
        self.result = {
            "findings": [{"source_id": 2, "text": "正文讨论音乐历史"}],
            "read_content_summary": "音乐历史内容概括",
            "evaluation": "我喜欢这种视角",
            "next_direction": "乐器历史",
        }
        self.llm = AsyncMock(side_effect=[dump(self.plan), dump(self.choice), dump(self.result)])

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def engine(self, **kw):
        return Explorer(
            self.store, self.search, self.llm, extract=self.extract, discover=self.discover, **kw
        )

    async def test_discovery_interleaves_candidates_and_reads_only_selection(self):
        record, _ = await self.engine().run()
        self.assertEqual(record["status"], "success")
        self.assertEqual(
            [s["url"] for s in record["candidates"]], [source(n)["url"] for n in [4, 1, 3, 2]]
        )
        self.assertEqual(len(record["sources"]), 1)
        self.assertEqual(record["sources"][0]["url"], source(1)["url"])
        self.assertEqual(record["sources"][0]["read_level"], "full_text")
        self.assertEqual(record["usage"], {"search_calls": 2, "extract_pages": 1, "llm_calls": 3})
        self.assertIn("真实正文", self.llm.call_args_list[2].args[0])
        self.assertIn("正文", format_record(record))
        self.assertEqual(len(self.store.read("activities.json", [])), 1)

    async def test_active_search_uses_one_query_no_rss(self):
        self.plan["exploration_mode"] = "active_search"
        self.llm.side_effect = [dump(self.plan), dump(self.choice), dump(self.result)]
        record, _ = await self.engine().run()
        self.assertEqual(record["status"], "success")
        self.discover.assert_not_awaited()
        self.assertEqual(self.search.await_count, 1)
        self.assertEqual(record["usage"]["search_calls"], 1)

    async def test_summary_fallback_is_explicit(self):
        self.extract.side_effect = lambda sources, **kw: (sources, [{"error_type": "TimeoutError"}])
        record, _ = await self.engine().run()
        self.assertEqual(record["read_level"], "summary")
        self.assertEqual(record["sources"][0]["read_content"], "摘要1")
        self.assertIn("摘要，未读正文", format_record(record))

    async def test_extract_disabled_still_reads_summaries(self):
        record, _ = await self.engine(max_extract_pages=0).run()
        self.assertEqual(record["status"], "success")
        self.extract.assert_not_awaited()
        self.assertEqual(record["usage"]["extract_pages"], 0)

    async def test_false_selection_rejected_before_extract(self):
        self.choice["selected"][0]["source_id"] = 99
        self.llm.side_effect = [dump(self.plan), dump(self.choice)]
        record, _ = await self.engine().run()
        self.assertEqual(record["status"], "failed")
        self.extract.assert_not_awaited()
        self.assertEqual(record["sources"], [])

    async def test_empty_selection_is_saved_without_fabrication(self):
        self.llm.side_effect = [dump(self.plan), dump({"selected": []})]
        record, _ = await self.engine().run()
        self.assertEqual(record["status"], "skipped")
        self.assertEqual(record["usage"]["llm_calls"], 2)
        self.extract.assert_not_awaited()

    async def test_budget_limits_fail_closed_before_unusable_extract(self):
        record, _ = await self.engine(max_search_calls=1, max_llm_calls=2).run()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(self.search.await_count, 1)
        self.assertEqual(self.llm.await_count, 2)
        self.extract.assert_not_awaited()

    async def test_daily_v03_survives_reload_and_claim(self):
        record, _ = await self.engine().run(scheduled_day="2026-11-03")
        duplicate, _ = await self.engine().run(scheduled_day="2026-11-03")
        self.assertEqual(record["status"], "success")
        self.assertIsNone(duplicate)
        self.assertEqual(len(Store(self.tmp.name).read("activities.json", [])), 1)
        self.assertEqual(self.llm.await_count, 3)

    async def test_rss_discovery_needs_no_search_call(self):
        self.choice["selected"][0]["source_id"] = 1
        self.result["findings"][0]["source_id"] = 1
        self.llm.side_effect = [dump(self.plan), dump(self.choice), dump(self.result)]
        record, _ = await self.engine(search_mode="rss").run()
        self.assertEqual(record["status"], "success")
        self.search.assert_not_awaited()
        self.assertEqual(record["usage"]["search_calls"], 0)

    async def test_two_selected_pages_one_extract_limit_is_mixed(self):
        choice = {
            "selected": [{"source_id": 1, "reason": "自然"}, {"source_id": 2, "reason": "音乐"}]
        }
        result = dict(
            self.result,
            findings=[{"source_id": 1, "text": "自然发现"}, {"source_id": 2, "text": "音乐发现"}],
        )
        self.llm.side_effect = [dump(self.plan), dump(choice), dump(result)]
        record, _ = await self.engine(max_extract_pages=1).run()
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["read_level"], "mixed")
        self.assertEqual([s["read_level"] for s in record["sources"]], ["full_text", "summary"])
        self.assertEqual(record["usage"]["extract_pages"], 1)

    async def test_config_hard_caps_reject_overspending(self):
        for key, value in [
            ("max_search_calls", 3),
            ("max_extract_pages", 3),
            ("max_llm_calls", 4),
            ("max_candidates", 13),
            ("max_content_chars", 12001),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.engine(**{key: value})
