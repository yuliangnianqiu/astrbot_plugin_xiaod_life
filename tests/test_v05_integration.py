"""V0.5 integration with bounded exploration and V0.4 private chat recall."""

import json
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_adapter
import test_v03_core
from test_interests import record


class ExplorationInterestTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_v03_core.DiscoveryTests.asyncSetUp
    asyncTearDown = test_v03_core.DiscoveryTests.asyncTearDown
    engine = test_v03_core.DiscoveryTests.engine

    def feedback(self, **changes):
        return dict(
            {
                "domain": "音乐",
                "sentiment": "positive",
                "reason": "喜欢文章中的采样思路",
                "source_id": 2,
            },
            **changes,
        )

    def responses(self, feedback):
        self.result["interest_feedback"] = feedback
        self.llm.side_effect = [
            test_v03_core.dump(self.plan),
            test_v03_core.dump(self.choice),
            test_v03_core.dump(self.result),
        ]

    async def test_third_stage_updates_interest_with_exactly_three_model_calls(self):
        self.responses(self.feedback())
        result, _ = await self.engine().run()
        state, logs = self.store.load()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["schema_version"], 5)
        self.assertEqual(state["weights"]["音乐"], 3.25)
        self.assertEqual(logs[0]["interest_feedback"], result["interest_feedback"])
        self.assertEqual(self.llm.await_count, 3)
        self.assertEqual(result["usage"]["llm_calls"], 3)
        self.assertIn("interest_feedback", self.llm.call_args_list[2].args[0])
        self.assertIn("interest_state", self.llm.call_args_list[2].args[0])

    async def test_planning_receives_weights_and_unfamiliar_domain_instruction(self):
        self.store.append(record("baseline"))
        self.responses(None)
        result, _ = await self.engine().run()
        self.assertEqual(result["status"], "success")
        prompt = self.llm.call_args_list[0].args[0]
        data = json.loads(prompt.splitlines()[-1])
        self.assertEqual(data["interest_weights"]["音乐"], 3.25)
        self.assertEqual(data["interest_weights"]["自然"], 3)
        self.assertIn("陌生领域", prompt)

    async def test_pending_domain_and_count_enter_planning(self):
        self.store.append(record("baseline", "电子音乐"))
        self.responses(self.feedback(domain="电子音乐文化"))
        result, _ = await self.engine().run()
        self.assertEqual(result["status"], "success")
        prompt = self.llm.call_args_list[0].args[0]
        data = json.loads(prompt.splitlines()[-1])
        self.assertEqual(data["pending_interests"], {"电子音乐": 1})
        self.assertEqual(self.store.load()[0]["weights"]["电子音乐"], 3)
        self.assertEqual(result["interest_feedback"]["domain"], "电子音乐")

    async def test_invalid_multi_feedback_fails_without_updating_interests(self):
        self.store.append(record("baseline"))
        before = self.store.load()[0]
        self.responses([self.feedback(), self.feedback(domain="自然")])
        result, _ = await self.engine().run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.load()[0], before)
        self.assertIsNone(result["interest_feedback"])
        self.assertEqual(self.llm.await_count, 3)

    async def test_feedback_on_unread_candidate_fails_without_updating(self):
        self.responses(self.feedback(source_id=3))
        result, _ = await self.engine().run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.load()[0]["weights"]["音乐"], 3)
        self.assertEqual(self.store.load()[0]["feedback_events"], {})

    async def test_skipped_and_failed_exploration_do_not_update_interests(self):
        self.store.append(record("baseline"))
        state = self.store.load()[0]
        self.llm.side_effect = [test_v03_core.dump(self.plan), '{"selected":[]}']
        skipped, _ = await self.engine().run()
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(self.store.load()[0], state)
        self.llm.side_effect = RuntimeError("simulated provider failure")
        failed, _ = await self.engine().run()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.load()[0], state)


class InterestChatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_adapter.AdapterTests.asyncSetUp
    asyncTearDown = test_adapter.AdapterTests.asyncTearDown

    def request(self):
        return types.SimpleNamespace(
            extra_user_content_parts=[], contexts=[], prompt="原话", system_prompt="原有人设"
        )

    async def test_current_weights_and_formation_basis_in_temporary_chat_context(self):
        for activity_id in ("one", "two", "three"):
            self.plugin.store.append(record(activity_id, "电子音乐"))
        event = test_adapter.Event()
        event.message_str = "你为什么喜欢电子音乐，兴趣权重是多少"
        req = self.request()
        await self.plugin.inject_life_context(event, req)
        text = req.extra_user_content_parts[0].text
        self.assertIn("[小D当前兴趣]", text)
        self.assertIn('"weight": 3.25', text)
        self.assertIn('"activity_id": "one"', text)
        self.assertIn('"activity_id": "two"', text)
        self.assertIn("https://example.com/music", text)
        self.assertTrue(req.extra_user_content_parts[0].temporary)
        self.assertEqual(req.prompt, "原话")
        self.assertEqual(req.system_prompt, "原有人设")
        self.context.llm_generate.assert_not_called()
        self.assertIsNone(self.plugin.client.session)

    async def test_interest_only_request_is_idempotent(self):
        event = test_adapter.Event()
        event.message_str = "你有什么兴趣爱好"
        req = self.request()
        await self.plugin.inject_life_context(event, req)
        await self.plugin.inject_life_context(event, req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertTrue(req.extra_user_content_parts[0].text.startswith("[小D当前兴趣]"))

    async def test_interest_queries_use_existing_private_access_scope(self):
        for kind in ("nonadmin", "group", "other_platform"):
            event = test_adapter.Event(admin=kind != "nonadmin")
            event.message_str = "兴趣是什么"
            if kind == "group":
                event.is_private_chat = lambda: False
            if kind == "other_platform":
                event.get_platform_name = lambda: "telegram"
            with self.subTest(kind=kind), patch.object(self.plugin.store, "load") as load:
                req = self.request()
                await self.plugin.inject_life_context(event, req)
                load.assert_not_called()
                self.assertEqual(req.extra_user_content_parts, [])

    async def test_chat_read_recovers_interrupted_interest_update(self):
        original = self.plugin.store.write

        def interrupted(name, value):
            if name == "interests.json":
                raise OSError("simulated interrupted save")
            original(name, value)

        with (
            patch.object(self.plugin.store, "write", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            self.plugin.store.append(record("one"))
        event = test_adapter.Event()
        event.message_str = "你的兴趣权重是什么"
        req = self.request()
        await self.plugin.inject_life_context(event, req)
        self.assertIn('"weight": 3.25', req.extra_user_content_parts[0].text)
        self.assertFalse((self.plugin.store.directory / "pending_commit.json").exists())

    async def test_legacy_initial_interests_answer_preserves_existing_file(self):
        self.plugin.store.write("interests.json", {"interests": ["音乐"]})
        path = self.plugin.store.directory / "interests.json"
        before = path.read_bytes()
        event = test_adapter.Event()
        event.message_str = "你喜欢什么"
        req = self.request()
        await self.plugin.inject_life_context(event, req)
        self.assertIn('"weight": 3.0', req.extra_user_content_parts[0].text)
        self.assertEqual(path.read_bytes(), before)

    async def test_plugin_initialize_recovers_before_network_start(self):
        original = self.plugin.store.write

        def interrupted(name, value):
            if name == "interests.json":
                raise OSError("simulated interrupted save")
            original(name, value)

        with (
            patch.object(self.plugin.store, "write", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            self.plugin.store.append(record("one"))

        async def start():
            self.assertFalse((self.plugin.store.directory / "pending_commit.json").exists())
            self.assertEqual(self.plugin.store.load()[0]["weights"]["音乐"], 3.25)

        self.plugin.config["auto_enabled"] = False
        self.plugin.client.start = AsyncMock(side_effect=start)
        await self.plugin.initialize()
        self.plugin.client.start.assert_awaited_once()
