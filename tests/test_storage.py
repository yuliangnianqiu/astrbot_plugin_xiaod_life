import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from core import Store, durable_io, message_chunks, short_string


class StorageTests(unittest.TestCase):
    def test_archive_keeps_complete_history_and_recent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            old = [{"id": str(i)} for i in range(1000)]
            store.write("activities.json", old)
            store.append({"id": "new"})
            archives = list(Path(tmp).glob("archive-*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(store.read(archives[0].name, []), old)
            self.assertEqual(store.read("activities.json", []), old[-5:] + [{"id": "new"}])

    def test_model_links_and_cq_are_rejected(self):
        for value in ["https://fake.example", "www.fake.example", "[CQ:at,qq=all]"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                short_string(value)

    def test_long_url_is_rejected_before_output(self):
        from network import public_url

        with self.assertRaises(ValueError):
            public_url("https://example.com/" + "a" * 1800)

    def test_chunks_keep_urls_intact(self):
        url = "https://example.com/" + "a" * 1000
        parts = list(message_chunks("x" * 1700 + "\n" + url))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[1], url)
        self.assertTrue(all(len(part) <= 1800 for part in parts))


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_disk_worker(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def write():
            started.set()
            release.wait(timeout=3)
            finished.set()

        task = asyncio.create_task(durable_io(write))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
