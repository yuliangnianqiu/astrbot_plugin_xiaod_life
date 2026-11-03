import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from core import Explorer, Store, format_recent, json_object
from network import PublicResolver, parse_feed, public_url


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.source = {
            "url": "https://example.com/science",
            "title": "Space discovery",
            "excerpt": "Space discovery excerpt",
            "feed_url": "https://example.com/rss",
            "published_at": "",
            "evidence_type": "rss_excerpt",
        }
        self.search = AsyncMock(return_value=([self.source], []))
        self.llm = AsyncMock(
            side_effect=[
                json.dumps({"topic": "太空", "keywords": ["space"]}),
                json.dumps(
                    {
                        "findings": [{"source_id": 1, "text": "摘录讨论太空"}],
                        "evaluation": "我觉得有趣",
                        "next_direction": "更多科学",
                    }
                ),
            ]
        )
        self.engine = Explorer(self.store, self.search, self.llm)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_success_persists_evidence_and_separate_opinion(self):
        record, message = await self.engine.run()
        self.assertIsNone(message)
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["sources"][0]["url"], self.source["url"])
        self.assertIn("retrieved_at", record["sources"][0])
        self.assertEqual(self.llm.await_count, 2)
        self.assertEqual(self.store.read("activities.json", [])[0], record)
        self.assertIn("最近真实探索", format_recent([record]))

    async def test_search_failure_preserves_previous(self):
        self.store.append({"status": "old"})
        self.search.side_effect = TimeoutError()
        record, _ = await self.engine.run()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(self.store.read("activities.json", [])[0], {"status": "old"})
        self.assertEqual(len(self.store.read("activities.json", [])), 2)

    async def test_no_matches_is_failure(self):
        self.search.return_value = ([], [])
        record, _ = await self.engine.run()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(self.llm.await_count, 1)

    async def test_invalid_model_output(self):
        self.llm.side_effect = ["not JSON"]
        record, _ = await self.engine.run()
        self.assertEqual(record["status"], "failed")
        self.search.assert_not_called()

    async def test_fabricated_source_id_rejected(self):
        self.llm.side_effect = [
            json.dumps({"topic": "太空", "keywords": ["space"]}),
            json.dumps(
                {
                    "findings": [{"source_id": 99, "text": "假资料"}],
                    "evaluation": "评价",
                    "next_direction": "继续",
                }
            ),
        ]
        record, _ = await self.engine.run()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["sources"][0]["url"], self.source["url"])

    async def test_source_limit_clamped(self):
        self.assertEqual(Explorer(self.store, self.search, self.llm, 99).max_sources, 3)

    async def test_corrupt_storage_untouched(self):
        path = Path(self.tmp.name) / "activities.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            await self.engine.run()
        self.assertEqual(path.read_text(), "{broken")
        self.llm.assert_not_called()

    async def test_concurrency_rejected(self):
        await self.engine.lock.acquire()
        try:
            result, message = await self.engine.run()
            self.assertIsNone(result)
            self.assertIn("正在进行", message)
            self.llm.assert_not_called()
        finally:
            self.engine.lock.release()

    async def test_cooldown_rejected(self):
        await self.engine.run()
        result, message = await self.engine.run()
        self.assertIsNone(result)
        self.assertIn("30", message)

    async def test_cancelled_run_saved(self):
        started = asyncio.Event()

        async def wait_forever(prompt):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.engine.run(wait_forever))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.store.read("activities.json", [])[0]["status"], "cancelled")

    async def test_provider_error_does_not_leak_secret(self):
        self.llm.side_effect = RuntimeError("secret-api-key")
        record, _ = await self.engine.run()
        self.assertNotIn("secret-api-key", json.dumps(record))
        self.assertEqual(record["errors"][-1]["error_type"], "RuntimeError")


class NetworkTests(unittest.TestCase):
    def test_unsafe_urls(self):
        for url in [
            "http://example.com",
            "https://127.0.0.1/a",
            "https://[::1]/",
            "https://10.0.0.1",
            "https://localhost/a",
            "https://foo.local/",
            "https://user:pass@example.com",
            "file:///etc/passwd",
            "https://example.com:8443/",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_safe_url(self):
        self.assertEqual(public_url("https://www.nasa.gov/feed/"), "https://www.nasa.gov/feed/")

    def test_rss_and_html(self):
        body = b"<rss><channel><item><title>Space</title><link>https://example.com/a</link><description>&lt;b&gt;space news&lt;/b&gt;</description></item></channel></rss>"
        entries = parse_feed(body, "https://example.com/rss")
        self.assertEqual(entries[0]["excerpt"], "space news")

    def test_atom_relative_url(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Space</title><link href="/a"/><summary>space news</summary></entry></feed>'
        self.assertEqual(
            parse_feed(body, "https://example.com/rss")[0]["url"], "https://example.com/a"
        )

    def test_entity_attack(self):
        body = b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><item><title>&xxe;</title></item></rss>'
        with self.assertRaises(Exception):
            parse_feed(body, "https://example.com/rss")

    def test_unsafe_links_skipped(self):
        body = b"<rss><item><title>x</title><link>https://127.0.0.1/</link><description>x</description></item></rss>"
        self.assertEqual(parse_feed(body, "https://example.com/rss"), [])

    def test_json_fence(self):
        self.assertEqual(json_object('```json\n{"x": 1}\n```'), {"x": 1})


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonpublic_dns_rejected(self):
        from unittest.mock import patch

        with patch(
            "aiohttp.resolver.DefaultResolver.resolve",
            AsyncMock(return_value=[{"host": "192.168.0.1"}]),
        ):
            resolver = PublicResolver()
            with self.assertRaises(OSError):
                await resolver.resolve("example.com")
            await resolver.close()

    async def test_search_deduplicates_and_bounds(self):
        from network import RSSClient

        client = RSSClient()
        client.fetch = AsyncMock(
            return_value=[
                {"url": "https://example.com/a", "title": "Space", "excerpt": "space"},
                {"url": "https://example.com/b", "title": "Other", "excerpt": "unrelated"},
            ]
        )
        results, errors = await client.search(["https://example.com/rss"] * 4, ["space"], 3)
        self.assertEqual(len(results), 1)
        self.assertEqual(client.fetch.await_count, 1)
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
