"""Independent boundary tests for Beijing daily eligibility and durable claims."""

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core import Explorer, Store
from scheduler import DailyScheduler


class DailyBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def tick_at(self, timestamp, enabled=True):
        days = []

        async def run(day):
            days.append(day)
            return {"status": "success"}, None

        scheduler = DailyScheduler(
            hour=15,
            minute=0,
            run=run,
            enabled=lambda: enabled,
            clock=lambda: datetime.fromisoformat(timestamp),
        )
        await scheduler.tick()
        return days

    async def test_before_beijing_due_time(self):
        self.assertEqual(await self.tick_at("2026-11-03T06:59:59+00:00"), [])

    async def test_exact_beijing_due_time(self):
        self.assertEqual(await self.tick_at("2026-11-03T07:00:00+00:00"), ["2026-11-03"])

    async def test_late_start_catches_up_today_only(self):
        self.assertEqual(await self.tick_at("2026-11-03T15:59:59+00:00"), ["2026-11-03"])

    async def test_beijing_midnight_is_not_previous_day(self):
        self.assertEqual(await self.tick_at("2026-11-03T16:00:00+00:00"), [])

    async def test_disabled_has_no_callback(self):
        self.assertEqual(await self.tick_at("2026-11-03T07:00:00+00:00", False), [])


class PersistentClaimTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_recreated_store_does_not_reclaim(self):
        self.assertTrue(self.store.claim_day("2026-11-03"))
        self.assertFalse(Store(self.tmp.name).claim_day("2026-11-03"))
        self.assertTrue(Store(self.tmp.name).claim_day("2026-11-04"))

    async def test_thread_race_has_exactly_one_winner(self):
        results = await asyncio.gather(
            *[asyncio.to_thread(Store(self.tmp.name).claim_day, "2026-11-03") for _ in range(20)]
        )
        self.assertEqual(sum(bool(x) for x in results), 1)

    async def test_existing_corrupt_claim_is_never_reset(self):
        directory = Path(self.tmp.name) / "scheduler"
        directory.mkdir()
        claim = directory / "2026-11-03.json"
        claim.write_bytes(b"{interrupted")
        self.assertFalse(self.store.claim_day("2026-11-03"))
        self.assertEqual(claim.read_bytes(), b"{interrupted")

    async def test_claim_preserves_old_interests_and_activities(self):
        for name, value in [
            ("interests.json", b'{"interests":["music"]}'),
            ("activities.json", b'[{"status":"success","topic":"old"}]'),
        ]:
            (Path(self.tmp.name) / name).write_bytes(value)
        before = {p.name: p.read_bytes() for p in Path(self.tmp.name).iterdir()}
        self.assertTrue(self.store.claim_day("2026-11-03"))
        for name, value in before.items():
            self.assertEqual((Path(self.tmp.name) / name).read_bytes(), value)


class EngineDailyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = PersistentClaimTests.asyncSetUp
    asyncTearDown = PersistentClaimTests.asyncTearDown

    async def test_failed_attempt_not_retried_after_restart(self):
        calls = []

        async def fail(prompt):
            calls.append(prompt)
            raise RuntimeError("SECRET-must-not-leak")

        first = Explorer(self.store, None, fail)
        result, _ = await first.run(scheduled_day="2026-11-03")
        self.assertEqual(result["status"], "failed")
        second = Explorer(Store(self.tmp.name), None, fail)
        result, message = await second.run(scheduled_day="2026-11-03")
        self.assertIsNone(result)
        self.assertIn("今日自动探索已尝试", message)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SECRET", (Path(self.tmp.name) / "activities.json").read_text())

    async def test_cancelled_attempt_not_retried_after_restart(self):
        started = asyncio.Event()

        async def wait(prompt):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(Explorer(self.store, None, wait).run(scheduled_day="2026-11-03"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.read("activities.json", [])[0]["status"], "cancelled")
        result, message = await Explorer(Store(self.tmp.name), None, wait).run(
            scheduled_day="2026-11-03"
        )
        self.assertIsNone(result)
        self.assertIn("今日自动探索已尝试", message)

    async def test_busy_does_not_consume_daily_claim(self):
        engine = Explorer(self.store, None, None)
        async with engine.lock:
            result, _ = await engine.run(scheduled_day="2026-11-03")
        self.assertIsNone(result)
        self.assertFalse((Path(self.tmp.name) / "scheduler" / "2026-11-03.json").exists())

    async def test_manual_failure_does_not_consume_daily_claim(self):
        async def fail(prompt):
            raise RuntimeError()

        await Explorer(self.store, None, fail).run()
        self.assertFalse((Path(self.tmp.name) / "scheduler").exists())


class RetryBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_then_success_stops_further_same_day_ticks(self):
        calls = []

        async def run(day):
            calls.append(day)
            return (
                (None, "已有探索正在进行，请稍后查看。")
                if len(calls) == 1
                else ({"status": "failed"}, None)
            )

        scheduler = DailyScheduler(
            run=run,
            enabled=lambda: True,
            clock=lambda: datetime.fromisoformat("2026-11-03T07:00:00+00:00"),
        )
        await scheduler.tick()
        await scheduler.tick()
        await scheduler.tick()
        self.assertEqual(len(calls), 2)

    async def test_persisted_duplicate_stops_same_day_ticks(self):
        calls = []

        async def run(day):
            calls.append(day)
            return None, "今日自动探索已尝试，不重复执行。"

        scheduler = DailyScheduler(
            run=run,
            enabled=lambda: True,
            clock=lambda: datetime.fromisoformat("2026-11-03T07:00:00+00:00"),
        )
        await scheduler.tick()
        await scheduler.tick()
        self.assertEqual(len(calls), 1)

    async def test_naive_clock_rejected(self):
        scheduler = DailyScheduler(
            run=None, enabled=lambda: True, clock=lambda: datetime(2026, 11, 3, 15)
        )
        with self.assertRaises(ValueError):
            await scheduler.tick()
