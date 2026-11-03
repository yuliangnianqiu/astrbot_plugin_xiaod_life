"""V0.2 integration of topic planning, old state, and durable daily claims."""

import json
import tempfile
import unittest
from unittest.mock import AsyncMock

from core import DEFAULT_INTERESTS, Explorer, Store, format_recent


class V02CoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_diverse_interest_enters_search(self):
        for interest in DEFAULT_INTERESTS:
            with self.subTest(interest=interest), tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                store.write("interests.json", {"interests": [interest], "custom": "preserve"})
                store.append({"topic": "旧主题", "next_direction": "追查音乐历史"})
                llm = AsyncMock(
                    side_effect=[
                        json.dumps({"topic": interest, "keywords": [interest]}),
                        json.dumps(
                            {
                                "findings": [{"source_id": 1, "text": "摘要中的发现"}],
                                "evaluation": "我觉得有趣",
                                "next_direction": "下次方向",
                            }
                        ),
                    ]
                )
                search = AsyncMock(
                    return_value=(
                        [
                            {
                                "title": "真实标题",
                                "url": "https://example.org/a",
                                "excerpt": "真实摘要",
                                "evidence_type": "search_snippet",
                            }
                        ],
                        [],
                    )
                )
                record, _ = await Explorer(store, search, llm).run()
                self.assertEqual(record["status"], "success")
                self.assertEqual(search.call_args.args[1], [interest])
                prompt = llm.call_args_list[0].args[0]
                self.assertIn("追查音乐历史", prompt)
                self.assertIn(interest, prompt)
                self.assertEqual(store.read("interests.json", {})["custom"], "preserve")

    async def test_daily_failed_attempt_is_not_retried_after_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            llm = AsyncMock(side_effect=RuntimeError("do not save secrets"))
            search = AsyncMock()
            record, _ = await Explorer(store, search, llm).run(scheduled_day="2026-11-03")
            self.assertEqual(record["status"], "failed")
            duplicate, message = await Explorer(Store(tmp), search, llm).run(
                scheduled_day="2026-11-03"
            )
            self.assertIsNone(duplicate)
            self.assertIn("今日", message)
            self.assertEqual(llm.await_count, 1)
            self.assertEqual(len(store.read("activities.json", [])), 1)
            # Manual calls remain independent of the automatic claim.
            await Explorer(store, search, llm).run()
            self.assertEqual(llm.await_count, 2)

    def test_legacy_sparse_success_can_be_read(self):
        self.assertIn("旧主题", format_recent([{"status": "success", "topic": "旧主题"}]))
