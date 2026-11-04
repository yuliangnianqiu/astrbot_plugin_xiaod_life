"""V0.4 local recall, timestamp boundaries and adapter isolation."""

import copy
import json
import types
import unittest
from datetime import datetime
from unittest.mock import patch

import test_adapter
from test_adapter import Event

from core import format_recent
from recall import MAX_CONTEXT_CHARS, SHANGHAI, recall_context

CURRENT = datetime(2026, 11, 4, 18, tzinfo=SHANGHAI)


def activity(topic="自然声景与电子音乐", stamp="2026-11-04T02:00:00+00:00"):
    return {
        "id": topic,
        "started_at": stamp,
        "status": "success",
        "topic": topic,
        "sources": [
            {
                "id": 1,
                "title": "真实来源",
                "url": "https://example.com/music",
                "read_level": "full_text",
                "read_content": "RAW_BODY_SHOULD_STAY_ON_DISK",
            }
        ],
        "candidates": [{"url": "https://example.com/unread"}],
        "model_findings": [{"source_id": 1, "text": "文章讨论自然采样"}],
        "read_content_summary": "自然声景与音乐的联系",
        "subjective_evaluation": "我想多了解自然采样",
    }


class RecallTests(unittest.TestCase):
    def test_today_uses_shanghai_and_excludes_yesterday(self):
        logs = [
            activity("昨天的阅读", "2026-11-03T15:59:59+00:00"),
            activity("跨日的阅读", "2026-11-03T16:00:00+00:00"),
        ]
        text = recall_context(logs, "今天干嘛啦", CURRENT)
        self.assertIn("跨日的阅读", text)
        self.assertIn("2026-11-04T00:00:00+08:00", text)
        self.assertNotIn("昨天的阅读", text)

    def test_yesterday_is_explicitly_filtered(self):
        logs = [activity(), activity("昨日阅读", "2026-11-03T01:00:00Z")]
        text = recall_context(logs, "昨天看了什么", CURRENT)
        self.assertIn("昨日阅读", text)
        self.assertNotIn("自然声景与电子音乐", text)

    def test_source_followup_keeps_links_depth_and_opinion(self):
        text = recall_context([activity()], "在哪看到的？给我链接", CURRENT)
        for value in ("https://example.com/music", '"read_level": "full_text"', "主观评价"):
            self.assertIn(value, text)
        self.assertNotIn("RAW_BODY", text)
        self.assertNotIn("https://example.com/unread", text)

    def test_topic_recall_and_unrelated_chat(self):
        self.assertIn("自然采样", recall_context([activity()], "电子音乐有什么有趣的", CURRENT))
        self.assertEqual(recall_context([activity()], "帮我算一下三加五", CURRENT), "")

    def test_old_record_defaults_to_summary(self):
        old = activity()
        del old["sources"][0]["read_level"]
        del old["read_content_summary"]
        text = recall_context([old], "最近有什么发现", CURRENT)
        self.assertIn('"read_level": "summary"', text)

    def test_link_followup_prefers_topic_from_same_request_history(self):
        logs = [activity("自然声景", "2026-11-03T01:00:00Z")]
        logs += [activity(f"今日活动{i}") for i in range(4)]
        text = recall_context(logs, "在哪看到的", CURRENT, followup_text="刚聊过自然声景")
        self.assertIn("自然声景", text)
        self.assertNotIn("今日活动", text)

    def test_no_today_record_does_not_relabel_history(self):
        old = activity("旧活动", "2026-11-03T01:00:00Z")
        text = recall_context([old], "今天干嘛啦", CURRENT)
        self.assertIn("2026-11-04 当天没有", text)
        self.assertNotIn("旧活动", text)

    def test_seven_day_window_and_future_excluded(self):
        logs = [
            activity("太久以前", "2026-10-20T01:00:00Z"),
            activity("未来活动", "2026-11-05T01:00:00Z"),
            activity("最近阅读"),
        ]
        text = recall_context(logs, "最近读了什么", CURRENT)
        self.assertIn("最近阅读", text)
        self.assertNotIn("太久以前", text)
        self.assertNotIn("未来活动", text)

    def test_newest_three_in_timestamp_order_and_no_mutation(self):
        logs = [activity(f"活动{i}", f"2026-11-04T0{i}:00:00Z") for i in (3, 1, 4, 2)]
        before = copy.deepcopy(logs)
        text = recall_context(logs, "最近干嘛啦", CURRENT)
        summaries = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
        self.assertEqual([s["topic"] for s in summaries], ["活动4", "活动3", "活动2"])
        self.assertEqual(logs, before)

    def test_failed_skipped_running_and_cancelled_are_excluded(self):
        logs = [dict(activity(), status=s) for s in ("failed", "skipped", "running", "cancelled")]
        text = recall_context(logs, "今天读了什么", CURRENT)
        self.assertIn("没有匹配", text)
        self.assertNotIn("https://example.com/music", text)

    def test_context_budget_preserves_whole_urls_and_valid_json(self):
        record = activity("音" * 100)
        record["sources"] = [
            {
                "id": i,
                "title": "源" * 500,
                "url": "https://example.com/" + str(i) * 1770,
                "read_level": "summary",
            }
            for i in (1, 2, 3)
        ]
        record["subjective_evaluation"] = "感" * 600
        record["read_content_summary"] = "摘" * 1000
        record["model_findings"] = [{"source_id": i, "text": "归" * 600} for i in (1, 2, 3)]
        text = recall_context([record] * 3, "最近读了什么", CURRENT)
        self.assertLessEqual(len(text), MAX_CONTEXT_CHARS)
        data = json.loads(text.splitlines()[-1])
        self.assertEqual(
            [s["url"] for s in data["sources"]], [s["url"] for s in record["sources"][:2]]
        )
        self.assertEqual([f["source_id"] for f in data["model_findings"]], [1, 2])

    def test_invalid_timestamp_fails_loud(self):
        for stamp in ("broken", "2026-11-04T01:00:00"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                recall_context([activity(stamp=stamp)], "今天干嘛啦", CURRENT)

    def test_today_lists_all_successes_and_recent_lists_three(self):
        logs = [activity(f"今日活动{i}") for i in range(5)]
        today = format_recent(logs, day=CURRENT.date())
        recent = format_recent(logs)
        self.assertEqual(today.count("· 今日活动"), 5)
        self.assertEqual(recent.count("· 今日活动"), 3)
        self.assertIn("2026-11-04 10:00", today)

    def test_history_boundary_and_empty_today(self):
        logs = [activity(stamp="2026-11-03T15:59:59Z")]
        self.assertIn("今天还没有", format_recent(logs, day=CURRENT.date()))
        self.assertIn("/life recent", format_recent([], day=CURRENT.date()))
        self.assertIn("时间未知", format_recent([{"status": "success", "topic": "旧主题"}]))


class RecallAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_adapter.AdapterTests.asyncSetUp
    asyncTearDown = test_adapter.AdapterTests.asyncTearDown

    def request(self):
        return types.SimpleNamespace(
            prompt="原始用户输入",
            system_prompt="原有人设",
            contexts=[{"role": "user"}],
            extra_user_content_parts=[],
        )

    async def inject(self, event, req):
        self.plugin.store.write("activities.json", [activity()])
        with patch.object(self.module, "datetime") as clock:
            clock.now.return_value = CURRENT
            await self.plugin.inject_life_context(event, req)

    async def test_temporary_append_preserves_other_request_fields_and_idempotence(self):
        req = self.request()
        other = self.module.TextPart(text="另一插件的记忆")
        req.extra_user_content_parts.append(other)
        event = Event()
        await self.inject(event, req)
        await self.inject(event, req)
        self.assertEqual(len(req.extra_user_content_parts), 2)
        self.assertIs(req.extra_user_content_parts[0], other)
        self.assertTrue(req.extra_user_content_parts[1].temporary)
        self.assertEqual(req.prompt, "原始用户输入")
        self.assertEqual(req.system_prompt, "原有人设")
        self.assertEqual(req.contexts, [{"role": "user"}])
        self.assertFalse(event.stopped)
        self.context.llm_generate.assert_not_called()
        self.assertIsNone(self.plugin.client.session)

    async def test_default_denies_nonadmin_group_and_other_platform_before_read(self):
        nonadmin, group, other = Event(False), Event(), Event()
        group.is_private_chat = lambda: False
        other.get_platform_name = lambda: "telegram"
        self.plugin.config["admin_only"] = False
        for event in (nonadmin, group, other):
            with self.subTest(event=event), patch.object(self.plugin.store, "read") as read:
                req = self.request()
                await self.plugin.inject_life_context(event, req)
                read.assert_not_called()
                self.assertEqual(req.extra_user_content_parts, [])

    async def test_exact_allowlist_separates_platform_instances_and_users(self):
        self.plugin.recall_sessions = [Event.unified_msg_origin]
        req = self.request()
        await self.inject(Event(False), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        for origin in ("other-qq:FriendMessage:123", "qq-platform:FriendMessage:456"):
            event = Event()
            event.unified_msg_origin = origin
            with patch.object(self.plugin.store, "read") as read:
                await self.plugin.inject_life_context(event, self.request())
                read.assert_not_called()

    async def test_disabled_closing_and_life_commands_skip_read(self):
        for key in ("enabled", "recall_enabled"):
            self.plugin.config[key] = False
            with patch.object(self.plugin.store, "read") as read:
                await self.plugin.inject_life_context(Event(), self.request())
                read.assert_not_called()
            self.plugin.config[key] = True
        self.plugin.closing = True
        with patch.object(self.plugin.store, "read") as read:
            await self.plugin.inject_life_context(Event(), self.request())
            read.assert_not_called()
        self.plugin.closing = False
        event = Event()
        event.message_str = "/life today"
        with patch.object(self.plugin.store, "read") as read:
            await self.plugin.inject_life_context(event, self.request())
            read.assert_not_called()

    async def test_corrupt_file_raises_preserves_bytes_and_request(self):
        path = self.plugin.store.directory / "activities.json"
        path.write_text("{broken", encoding="utf-8")
        req = self.request()
        with self.assertRaises(json.JSONDecodeError):
            await self.plugin.inject_life_context(Event(), req)
        self.assertEqual(path.read_text(), "{broken")
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_unrelated_chat_leaves_request_unchanged(self):
        event = Event()
        event.message_str = "三加五等于几"
        req = self.request()
        await self.inject(event, req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_source_followup_reads_only_current_request_history(self):
        event = Event()
        event.message_str = "原文链接呢"
        req = self.request()
        req.contexts = [{"role": "assistant", "content": "自然声景与电子音乐"}]
        await self.inject(event, req)
        self.assertIn("https://example.com/music", req.extra_user_content_parts[0].text)

    async def test_history_and_explore_commands_share_private_scope(self):
        self.plugin.config["admin_only"] = False
        event = Event()
        event.is_private_chat = lambda: False
        for command in (self.plugin.today, self.plugin.recent, self.plugin.explore):
            event.stopped = False
            with patch.object(self.plugin.store, "read") as read:
                replies = [r async for r in command(event)]
                read.assert_not_called()
            self.assertTrue(replies)
            self.assertTrue(event.stopped)

    async def test_recent_keeps_history_after_plugin_reload(self):
        self.plugin.store.write("activities.json", [activity("旧活动", "2026-10-01T01:00:00Z")])
        reloaded = self.module.XiaodLife(self.context, {})
        try:
            result = "".join([r async for r in reloaded.recent(Event())])
            self.assertIn("旧活动", result)
            self.assertIn("2026-10-01 09:00", result)
        finally:
            await reloaded.terminate()

    async def test_today_actual_date_and_chunk_propagation(self):
        self.plugin.store.write(
            "activities.json", [activity(), activity("旧活动", "2026-10-01T01:00:00Z")]
        )
        event = Event()
        with patch.object(self.module, "datetime") as clock:
            clock.now.return_value = CURRENT
            replies = []
            async for result in self.plugin.today(event):
                self.assertFalse(event.stopped)
                replies.append(result)
        self.assertTrue(event.stopped)
        self.assertIn("自然声景", "".join(replies))
        self.assertNotIn("旧活动", "".join(replies))

    async def test_invalid_allowlist_rejected(self):
        for value in ("qq-platform:FriendMessage:123", [123], [""]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.XiaodLife(self.context, {"recall_sessions": value})
