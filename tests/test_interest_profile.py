"""Combined V0.5 requirements: bounded main profile, history and natural recall."""

import copy
import json
import tempfile
import unittest

import test_adapter
from test_interests import record

from core import Store
from interests import (
    MAX_INTEREST_CONTEXT,
    interest_context,
    interest_profile,
    main_interests,
    normalize_state,
    planning_interests,
)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def test_feedback_counts_and_last_time_are_derived_once(self):
        for activity_id, sentiment, stamp in (
            ("one", "positive", "2026-11-04T03:00:00+00:00"),
            ("two", "negative", "2026-11-04T12:00:00+08:00"),
            ("three", "neutral", "2026-11-04T02:00:00+00:00"),
        ):
            item = dict(record(activity_id, sentiment=sentiment), finished_at=stamp)
            self.store.append(item)
            self.store.append(item)
        state, _ = self.store.load()
        before = copy.deepcopy(state)
        profile = interest_profile(state)["音乐"]
        self.assertEqual(profile["positive_count"], 1)
        self.assertEqual(profile["negative_count"], 1)
        self.assertEqual(profile["neutral_count"], 1)
        self.assertEqual(profile["last_explored_at"], "2026-11-04T12:00:00+08:00")
        self.assertEqual(profile["weight"], 3)
        self.assertEqual(state, before)

    def test_twenty_legacy_interests_keep_all_data_with_twelve_main_entries(self):
        names = [f"领域{i}" for i in range(20)]
        self.store.write("interests.json", {"interests": names, "custom": "旧数据"})
        path = self.store.directory / "interests.json"
        before = path.read_bytes()
        state, _ = self.store.load()
        plan = planning_interests(state)
        self.assertEqual(len(plan["interests"]), 12)
        self.assertEqual(len(plan["interest_profile"]), 12)
        self.assertEqual(len(plan["interest_weights"]), 12)
        self.assertEqual(state["interests"], names)
        self.assertEqual(state["custom"], "旧数据")
        self.assertEqual(path.read_bytes(), before)

    def test_maintenance_considers_weight_then_feedback_then_recency(self):
        state = normalize_state({"interests": [f"领域{i}" for i in range(13)]})
        self.store.write("interests.json", state)
        self.assertNotIn("领域12", planning_interests(state)["interests"])
        self.store.append(record("positive", "领域12"))
        updated, _ = self.store.load()
        self.assertEqual(planning_interests(updated)["interests"][0], "领域12")
        self.assertEqual(len(updated["interests"]), 13)
        self.store.append(record("negative", "领域12", "negative"))
        updated, _ = self.store.load()
        self.assertEqual(updated["weights"]["领域12"], 3)
        # Equal weight and net feedback: the related recent exploration breaks the tie.
        self.assertEqual(planning_interests(updated)["interests"][0], "领域12")

    def test_cumulative_feedback_breaks_an_equal_weight_tie(self):
        self.store.append(record("music"))
        state, _ = self.store.load()
        state["weights"]["电影"] = 3.25
        profiles = interest_profile(state)
        self.assertEqual(main_interests(state, profiles)[0], "音乐")

    def test_low_weight_retained_domain_can_reenter(self):
        self.store.write("interests.json", {"interests": [f"领域{i}" for i in range(13)]})
        self.store.append(record("negative", "领域12", "negative"))
        state, _ = self.store.load()
        self.assertNotIn("领域12", planning_interests(state)["interests"])
        self.store.append(record("positive", "领域12"))
        state, _ = self.store.load()
        self.assertIn("领域12", planning_interests(state)["interests"])
        self.assertEqual(len(state["interests"]), 13)

    def test_forming_thirteenth_interest_keeps_primary_cap_and_old_archive(self):
        names = [f"领域{i}" for i in range(12)]
        self.store.write("interests.json", {"interests": names})
        self.store.append(record("one", "新领域"))
        self.store.append(record("two", "新领域"))
        state, logs = self.store.load()
        main = planning_interests(state)["interests"]
        self.assertEqual(len(main), 12)
        self.assertIn("新领域", main)
        self.assertEqual(state["interests"], names + ["新领域"])
        self.assertTrue(all(state["weights"][n] == 3 for n in names))
        self.assertEqual(state["formation"]["新领域"], ["one", "two"])
        self.assertEqual(len(logs), 2)
        self.assertEqual(Store(self.tmp.name).load(), (state, logs))

    def test_recently_formed_interests_have_times_and_are_limited_to_three(self):
        for index in range(4):
            for suffix in ("a", "b"):
                self.store.append(
                    dict(
                        record(f"{index}{suffix}", f"新领域{index}"),
                        finished_at=f"2026-11-0{index + 1}T02:00:00+00:00",
                    )
                )
        state, _ = self.store.load()
        recent = planning_interests(state)["recently_formed_interests"]
        self.assertEqual([p["name"] for p in recent], ["新领域3", "新领域2", "新领域1"])
        self.assertEqual(recent[0]["positive_count"], 2)
        self.assertEqual(recent[0]["formed_at"], "2026-11-04T02:00:00+00:00")

    def test_candidate_planning_view_is_bounded_and_complete_state_survives(self):
        for index in range(15):
            self.store.append(record(f"candidate{index}", f"候选{index}"))
        state, _ = self.store.load()
        self.assertEqual(len(planning_interests(state)["pending_interests"]), 12)
        self.assertEqual(len(state["pending_interests"]), 15)

    def test_retained_domain_is_queryable_without_promoting_or_mutating_it(self):
        state = normalize_state({"interests": [f"领域{i}" for i in range(20)]})
        before = copy.deepcopy(state)
        text = interest_context(state, "领域19的兴趣权重是多少")
        self.assertIn('"name": "领域19"', text)
        self.assertIn('"is_main_interest": false', text)
        self.assertIn('"weight": 3.0', text)
        self.assertEqual(state, before)

    def test_new_hobby_query_prioritizes_formation_evidence(self):
        for activity_id in ("one", "two"):
            self.store.append(record(activity_id, "电子音乐"))
        state, _ = self.store.load()
        text = interest_context(state, "你有没有什么新爱好")
        self.assertIn('"name": "电子音乐"', text)
        self.assertIn('"activity_id": "one"', text)
        self.assertIn('"activity_id": "two"', text)
        self.assertIn("https://example.com/music", text)

    def test_new_hobby_query_reports_initial_basis_when_no_new_interest_formed(self):
        state = normalize_state({"interests": ["音乐"]})
        self.assertIn("尚未形成新的长期兴趣", interest_context(state, "你有新爱好吗"))

    def test_full_profile_chat_remains_bounded_with_long_names(self):
        state = normalize_state({"interests": [f"领域{i}" + "长" * 95 for i in range(20)]})
        text = interest_context(state, "你的兴趣权重是什么")
        self.assertLessEqual(len(text), MAX_INTEREST_CONTEXT)
        overview = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
        self.assertEqual(len(overview["interest_overview"]), 12)

    def test_named_formation_keeps_two_long_sources_and_bounded_reasons(self):
        for activity_id in ("one", "two"):
            item = record(activity_id, "电子音乐")
            item["interest_feedback"]["reason"] = "理" * 300
            item["sources"][0]["title"] = "题" * 200
            item["sources"][0]["url"] = "https://example.com/" + "a" * 1750
            self.store.append(item)
        state, _ = self.store.load()
        text = interest_context(state, "电子音乐这个兴趣的形成依据是什么")
        self.assertLessEqual(len(text), MAX_INTEREST_CONTEXT)
        self.assertIn('"activity_id": "one"', text)
        self.assertIn('"activity_id": "two"', text)
        self.assertIn("https://example.com/" + "a" * 1750, text)


class NaturalInterestChatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_adapter.AdapterTests.asyncSetUp
    asyncTearDown = test_adapter.AdapterTests.asyncTearDown

    async def test_all_four_requested_question_styles_receive_temporary_interest_context(self):
        import types

        for activity_id in ("one", "two"):
            self.plugin.store.append(record(activity_id, "电子音乐"))
        for question in (
            "小D，你最近喜欢什么？",
            "你有没有什么新爱好？",
            "你最近怎么总在研究音乐呀？",
            "你以前不是对电影更感兴趣吗？",
        ):
            with self.subTest(question=question):
                event = test_adapter.Event()
                event.message_str = question
                req = types.SimpleNamespace(extra_user_content_parts=[], contexts=[])
                await self.plugin.inject_life_context(event, req)
                self.assertIn("[小D当前兴趣]", req.extra_user_content_parts[0].text)
                self.assertIn("positive_count", req.extra_user_content_parts[0].text)
                self.assertTrue(req.extra_user_content_parts[0].temporary)
        self.context.llm_generate.assert_not_called()
