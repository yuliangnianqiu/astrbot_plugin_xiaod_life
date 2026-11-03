"""Independent V0.2 background lifecycle and provider contract tests."""

import asyncio
import types
import unittest
from unittest.mock import AsyncMock

import test_adapter


class BackgroundReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_adapter.AdapterTests.asyncSetUp
    asyncTearDown = test_adapter.AdapterTests.asyncTearDown

    async def test_background_provider_needs_no_message_event(self):
        self.plugin.config["provider_id"] = "background-provider"
        self.assertEqual(await self.plugin.generate("prompt"), "{}")
        self.context.get_current_chat_provider_id.assert_not_called()
        self.assertEqual(
            self.context.llm_generate.call_args.kwargs["chat_provider_id"], "background-provider"
        )
        self.assertEqual(self.context.llm_generate.call_args.kwargs["contexts"], [])
        self.assertIsNone(self.context.llm_generate.call_args.kwargs["tools"])

    async def test_missing_background_provider_does_not_pick_chat(self):
        with self.assertRaises(ValueError):
            await self.plugin.generate("prompt")
        self.context.get_current_chat_provider_id.assert_not_called()
        self.context.llm_generate.assert_not_called()

    async def test_scheduled_uses_same_engine_and_generator(self):
        run = AsyncMock(return_value=({"status": "success"}, None))
        self.plugin.engine = types.SimpleNamespace(run=run)
        result, _ = await self.plugin.run_scheduled("2026-11-03")
        self.assertEqual(result["status"], "success")
        run.assert_awaited_once_with(self.plugin.generate, scheduled_day="2026-11-03")

    async def test_background_storage_error_is_sanitized_and_returned(self):
        self.plugin.engine = types.SimpleNamespace(run=AsyncMock(side_effect=ValueError("SECRET")))
        with self.assertLogs("adapter-test", level="ERROR") as logs:
            result, message = await self.plugin.run_scheduled("2026-11-03")
        self.assertIsNone(result)
        self.assertNotIn("SECRET", message + str(logs.output))

    async def test_terminate_waits_for_background_finally_before_client_close(self):
        ready = asyncio.Event()
        finalized = []

        async def background():
            try:
                ready.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finalized.append("disk")

        async def close():
            self.assertEqual(finalized, ["disk"])
            finalized.append("close")

        self.plugin.scheduler_task = asyncio.create_task(background())
        await ready.wait()
        self.plugin.client.close = AsyncMock(side_effect=close)
        await self.plugin.terminate()
        self.assertTrue(self.plugin.scheduler_task.cancelled())
        self.assertEqual(finalized, ["disk", "close"])
        self.plugin.client.close = AsyncMock()

    async def test_concurrent_initialize_only_starts_one_scheduler(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def start():
            entered.set()
            await release.wait()

        self.plugin.client.start = AsyncMock(side_effect=start)
        self.plugin.config["auto_enabled"] = False
        first = asyncio.create_task(self.plugin.initialize())
        await entered.wait()
        second = asyncio.create_task(self.plugin.initialize())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        tasks = [t for t in asyncio.all_tasks() if t.get_name() == "xiaod-life-daily"]
        try:
            self.assertEqual(len(tasks), 1)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_terminate_during_initialize_leaves_no_live_scheduler(self):
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def start():
            entered.set()
            await release.wait()
            order.append("start")

        async def close():
            order.append("close")

        self.plugin.client.start = AsyncMock(side_effect=start)
        self.plugin.client.close = AsyncMock(side_effect=close)
        initializing = asyncio.create_task(self.plugin.initialize())
        await entered.wait()
        terminating = asyncio.create_task(self.plugin.terminate())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(initializing, terminating)
        self.assertEqual(order, ["start", "close"])
        self.assertTrue(self.plugin.scheduler_task.done())
        await self.plugin.initialize()
        self.assertEqual(self.plugin.client.start.await_count, 1)
