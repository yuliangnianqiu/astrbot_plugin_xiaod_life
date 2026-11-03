"""Adapter contract tests with explicit AstrBot API doubles, not a live bot."""

import asyncio
import importlib.util
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_adapter(directory):
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object

    def group(_):
        def decorate(fn):
            fn.command = lambda _: lambda handler: handler
            return fn

        return decorate

    event.filter = types.SimpleNamespace(command_group=group)
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context
            self.logger = logging.getLogger("adapter-test")

    star.Star, star.Context = Star, object
    star.StarTools = types.SimpleNamespace(get_data_dir=lambda name: Path(directory))
    package = types.ModuleType("_xiaod_review")
    package.__path__ = [str(ROOT)]
    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "_xiaod_review": package,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("_xiaod_review.main", ROOT / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Event:
    unified_msg_origin = "qq-platform:FriendMessage:123"

    def __init__(self, admin=True):
        self.admin = admin
        self.stopped = False

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module = load_adapter(self.tmp.name)
        self.context = types.SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="deepseek"),
            llm_generate=AsyncMock(return_value=types.SimpleNamespace(completion_text="{}")),
        )
        self.plugin = self.module.XiaodLife(self.context, {})

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.tmp.cleanup()

    async def test_nonadmin_denied_without_model_or_network(self):
        event = Event(False)
        result = [x async for x in self.plugin.explore(event)]
        self.assertTrue(event.stopped)
        self.assertIn("管理员", "".join(result))
        self.context.llm_generate.assert_not_called()
        self.assertIsNone(self.plugin.client.session)

    async def test_disabled_denied(self):
        self.plugin.config["enabled"] = False
        result = [x async for x in self.plugin.explore(Event())]
        self.assertIn("停用", "".join(result))
        self.context.llm_generate.assert_not_called()

    async def test_provider_contract(self):
        async def run(generate):
            self.assertEqual(await generate("prompt"), "{}")
            return None, "done"

        self.plugin.engine = types.SimpleNamespace(run=run)
        self.assertEqual([x async for x in self.plugin.explore(Event())], ["done"])
        self.context.get_current_chat_provider_id.assert_awaited_once_with(
            umo=Event.unified_msg_origin
        )
        kwargs = self.context.llm_generate.call_args.kwargs
        self.assertEqual(kwargs["chat_provider_id"], "deepseek")
        self.assertEqual(kwargs["contexts"], [])
        self.assertIsNone(kwargs["tools"])
        self.assertFalse(self.plugin.running)

    async def test_terminate_cancels_active_and_closes_client(self):
        task = asyncio.create_task(asyncio.Event().wait())
        self.plugin.running.add(task)
        self.plugin.client.close = AsyncMock()
        await self.plugin.terminate()
        self.assertTrue(task.cancelled())
        self.assertTrue(self.plugin.closing)
        self.plugin.client.close.assert_awaited_once()

    async def test_today_does_not_call_model(self):
        result = [x async for x in self.plugin.today(Event())]
        self.assertIn("没有", "".join(result))
        self.context.llm_generate.assert_not_called()


class YieldPropagationTests(unittest.IsolatedAsyncioTestCase):
    """Check the scheduler's stop guard at every yielded response boundary."""

    asyncSetUp = AdapterTests.asyncSetUp
    asyncTearDown = AdapterTests.asyncTearDown

    async def collect_deliverable(self, generator, event):
        delivered = []
        async for response in generator:
            self.assertFalse(event.stopped, "scheduler would suppress this yielded reply")
            delivered.append(response)
        self.assertTrue(event.stopped, "command should stop propagation only after sending")
        return delivered

    async def test_denial_deliverable(self):
        event = Event(False)
        replies = await self.collect_deliverable(self.plugin.explore(event), event)
        self.assertIn("管理员", "".join(replies))

    async def test_disabled_deliverable(self):
        self.plugin.config["enabled"] = False
        event = Event()
        replies = await self.collect_deliverable(self.plugin.explore(event), event)
        self.assertIn("停用", "".join(replies))

    async def test_not_ready_deliverable(self):
        event = Event()
        replies = await self.collect_deliverable(self.plugin.explore(event), event)
        self.assertIn("就绪", "".join(replies))

    async def test_success_all_parts_deliverable(self):
        message = "\n".join(["a" * 1000, "b" * 1000, "c" * 1000])
        self.plugin.engine = types.SimpleNamespace(run=AsyncMock(return_value=(None, message)))
        event = Event()
        replies = await self.collect_deliverable(self.plugin.explore(event), event)
        self.assertEqual(len(replies), 3)
        self.assertEqual("\n".join(replies), message)

    async def test_explore_exception_deliverable(self):
        self.plugin.engine = types.SimpleNamespace(run=AsyncMock(side_effect=ValueError()))
        event = Event()
        replies = await self.collect_deliverable(self.plugin.explore(event), event)
        self.assertIn("运行错误", "".join(replies))

    async def test_today_all_parts_deliverable(self):
        message = "\n".join(["a" * 1000, "b" * 1000, "c" * 1000])
        event = Event()
        with patch.object(self.module, "format_recent", return_value=message):
            replies = await self.collect_deliverable(self.plugin.today(event), event)
        self.assertEqual(len(replies), 3)
        self.assertEqual("\n".join(replies), message)

    async def test_today_exception_deliverable(self):
        event = Event()
        with patch.object(self.plugin.store, "read", side_effect=ValueError()):
            replies = await self.collect_deliverable(self.plugin.today(event), event)
        self.assertIn("无法读取", "".join(replies))
